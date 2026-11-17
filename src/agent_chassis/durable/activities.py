import json
from collections import deque

from opentelemetry import trace
from temporalio import activity

from agent_chassis.audit.base import AuditEvent
from agent_chassis.channels.base import OutboundDelivery
from agent_chassis.contracts import (
    ContentPart,
    ConversationHistory,
    MessageEnvelope,
    OutcomeEvent,
)
from agent_chassis.durable.models import (
    ApprovalRecord,
    ApproverNotice,
    EscalationRecord,
    GuardrailCheck,
    GuardrailOutcome,
    OutputCheck,
)
from agent_chassis.durable.runtime_context import RoleRuntime
from agent_chassis.memory.base import DEFAULT_AUTHOR
from agent_chassis.packs.base import GuardrailPack, OutputGuardrailPack, OutputReply
from agent_chassis.packs.composition import compose_instructions
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)
_tracer = trace.get_tracer("agent_chassis.durable.activities")
_RECENT = 1000


def _attempt() -> int:
    """Which attempt of this activity is running (1 outside an activity, as in
    unit tests that call the method directly)."""
    try:
        return activity.info().attempt
    except RuntimeError:
        return 1


def _outcome_attributes(event: OutcomeEvent) -> dict[str, str | int | list[str]]:
    return {
        "session.id": event.conversation_id,
        "outcome.role": event.role,
        "outcome.tenant": event.tenant,
        "outcome.task_type": event.task_type,
        "outcome.resolution": event.resolution.value,
        "outcome.turns": event.turns,
        "outcome.tool_calls": event.tool_calls,
        "outcome.wall_time_ms": event.wall_time_ms,
        "outcome.tokens_in": event.tokens_in,
        "outcome.tokens_out": event.tokens_out,
        "outcome.escalations": event.escalations,
        "outcome.guardrail_trips": event.guardrail_trips,
        "outcome.models_used": list(event.models_used),
    }


def render_notice(notice: ApproverNotice) -> str:
    """The text an approver reads — enough to act on without opening a log.

    Internal by design: reasons and tool arguments appear here, which is why
    this goes to the approvers' own destination and never to a customer's.
    """
    if notice.kind == "escalation":
        lines = [
            f"[{notice.employee}] escalated a conversation to a human",
            f"Reason: {notice.reason}",
            f"Handled by agent: {notice.agent}",
        ]
        if notice.requester:
            lines.append(
                f"With: {notice.requester}"
                + (f" (via {notice.channel})" if notice.channel else "")
            )
        lines.append(f"Conversation: {notice.subject_id}")
        return "\n".join(lines)

    heading = (
        f"[{notice.employee}] reminder: still waiting for approval"
        if notice.kind == "reminder"
        else f"[{notice.employee}] needs approval to continue"
    )
    lines = [heading, f"Task run: {notice.subject_id}"]
    # A reminder is a nudge: the original notice already carried the arguments,
    # which can be long (a whole report body), so repeat only what is pending.
    lines += [
        f"- {item.tool}  (call {item.call_id})"
        if notice.kind == "reminder"
        else f"- {item.tool} {item.args}  (call {item.call_id})"
        for item in notice.pending
    ]
    lines.append(
        'Decide with POST /v1/approvals {"workflow_id": "'
        + notice.subject_id
        + '", "approved": true|false}'
        " (add call_id to decide one call; omit it to decide all)."
    )
    return "\n".join(lines)


class ChassisActivities:
    """Chassis-owned activities: guardrail pre-flight and output checks (D5),
    history compaction (D6a), outcome emission (D12), audit and escalation
    records (D11), approver notices (D10) and outbound delivery (D15).

    Outcomes, escalations and approvals are each a structured log line, and a
    row in an append-only Postgres table (``chassis_outcomes`` / ``chassis_audit``)
    when POSTGRES_URL is set. An approval's row is written first and fails
    closed; an outcome's is best-effort with bounded retries. The api reads the
    tables back through ``GET /v1/audit`` and ``GET /v1/outcomes``.
    """

    def __init__(self, runtime: RoleRuntime) -> None:
        self._runtime = runtime
        # In-process tail of what was emitted — a test/debug aid, not a
        # store. Bounded: a worker lives for weeks and these used to grow
        # without limit.
        self.emitted_outcomes: deque[OutcomeEvent] = deque(maxlen=_RECENT)
        self.escalation_records: deque[EscalationRecord] = deque(maxlen=_RECENT)
        self.approval_records: deque[ApprovalRecord] = deque(maxlen=_RECENT)

    @activity.defn
    async def run_guardrail(self, check: GuardrailCheck) -> GuardrailOutcome:
        resolved = next(
            g
            for g in self._runtime.resolved.input_guardrails
            if g.pack.name == check.guardrail
        )
        assert isinstance(resolved.pack, GuardrailPack)
        envelope = MessageEnvelope(
            sender=check.sender,
            tenant=check.tenant,
            conversation_id=check.conversation_id,
            channel=check.channel,
            parts=[ContentPart(text=check.text)],
        )
        # GUARDRAIL is an openinference span kind Phoenix renders natively;
        # without this span the check would be an invisible activity (the
        # packs' own LLM calls are theirs to instrument).
        with _tracer.start_as_current_span(
            f"guardrail {check.guardrail}",
            attributes={
                "openinference.span.kind": "GUARDRAIL",
                "session.id": check.conversation_id,
                "input.value": check.text,
            },
        ) as span:
            verdict = await resolved.pack.check(
                envelope, resolved.config, context=tuple(check.context)
            )
            span.set_attribute("guardrail.passed", verdict.passed)
            span.set_attribute(
                "output.value",
                "pass" if verdict.passed else f"trip: {verdict.reason}",
            )
        return GuardrailOutcome(
            guardrail=check.guardrail, passed=verdict.passed, reason=verdict.reason
        )

    @activity.defn
    async def run_output_guardrail(self, check: OutputCheck) -> GuardrailOutcome:
        """D5: judge one final reply before it reaches the user.

        The span carries the verdict, never the reply: a tripped reply is the
        thing that must not travel, so it stays out of traces and logs too.
        """
        resolved = next(
            g
            for g in self._runtime.resolved.output_guardrails
            if g.pack.name == check.guardrail
        )
        assert isinstance(resolved.pack, OutputGuardrailPack)
        resolved_role = self._runtime.resolved
        reply = OutputReply(
            text=check.reply,
            agent=check.agent,
            user_message=check.user_message,
            instructions=compose_instructions(resolved_role, check.agent),
            transcript=tuple(check.context),
            employee=resolved_role.manifest.employee.name,
        )
        with _tracer.start_as_current_span(
            f"guardrail {check.guardrail}",
            attributes={
                "openinference.span.kind": "GUARDRAIL",
                "session.id": check.conversation_id,
                "guardrail.stage": "output",
            },
        ) as span:
            verdict = await resolved.pack.check(reply, resolved.config)
            span.set_attribute("guardrail.passed", verdict.passed)
            span.set_attribute(
                "output.value",
                "pass" if verdict.passed else f"trip: {verdict.reason}",
            )
        if not verdict.passed:
            _logger.warning(
                "output guardrail %s tripped conversation=%s agent=%s: %s",
                check.guardrail,
                check.conversation_id,
                check.agent,
                verdict.reason,
            )
        return GuardrailOutcome(
            guardrail=check.guardrail, passed=verdict.passed, reason=verdict.reason
        )

    @activity.defn
    async def compact_history(
        self, history: ConversationHistory
    ) -> ConversationHistory:
        return await self._runtime.adapter.compact_history(
            self._runtime.handle,
            history,
            self._runtime.resolved.manifest.runtime.history_budget,
        )

    @activity.defn
    async def emit_outcome(self, event: OutcomeEvent) -> None:
        """D12: the finished conversation or task becomes a log line, a span, a
        row in the outcome table and a memory entry.

        The log line carries the whole event and is written first, once: it is
        the source a lost row can be backfilled from. The row write raises on a
        store failure so Temporal retries it; the workflow bounds the retries
        and then gives up (an outcome is a metric, not an accountability record,
        so it never holds a workflow open). The memory entry is written only once
        the row has landed, so retries do not duplicate it.
        """
        first_attempt = _attempt() == 1
        if first_attempt:
            self.emitted_outcomes.append(event)
            _logger.info("outcome_event %s", json.dumps(event.model_dump(mode="json")))
        # A dedicated span: an outcome is emitted when a conversation idles out,
        # when no request trace is active, so there is no span to hang an event on.
        with _tracer.start_as_current_span(
            f"outcome {event.task_type}", attributes=_outcome_attributes(event)
        ):
            written = True
            if self._runtime.outcomes is not None:
                written = await self._runtime.outcomes.record(event)
        if written and (first_attempt or self._runtime.outcomes is not None):
            await self._write_back(
                "outcome",
                f"Conversation {event.conversation_id} ended {event.resolution}:"
                f" {event.turns} turns, {event.escalations} escalations.",
                {"conversation_id": event.conversation_id},
            )

    @activity.defn
    async def record_escalation(self, record: EscalationRecord) -> None:
        self.escalation_records.append(record)
        _logger.info("escalation %s", json.dumps(record.model_dump(mode="json")))
        # Best-effort: an escalation is already acknowledged to the user and
        # pushed to the approvers, so an audit store outage must not fail the
        # turn. (Approvals are different: see record_approval.)
        await self._audit_best_effort(
            AuditEvent(
                kind="escalation",
                employee=self._employee(),
                tenant=record.tenant,
                subject=record.conversation_id,
                actor=self._employee(),
                action="reminded" if record.kind == "reminder" else "escalated",
                detail={
                    "agent": record.agent,
                    "reason": record.reason,
                    "payload": record.payload,
                },
                dedupe_key=f"{record.conversation_id}:{record.occurred_at.isoformat()}",
                occurred_at=record.occurred_at,
            )
        )
        await self._write_back(
            "event",
            f"Escalated conversation {record.conversation_id}: {record.reason}",
            {"conversation_id": record.conversation_id, "agent": record.agent},
        )

    @activity.defn
    async def record_approval(self, record: ApprovalRecord) -> None:
        """D11: approval decisions share the audit stream with manifest
        overrides — same log family, same store.

        Fail-closed: with an audit store configured, the entry must land before
        the workflow acts on the decision, so a failed write raises and Temporal
        retries until it succeeds. A database outage pauses approvals; it never
        lets one go unrecorded."""
        if self._runtime.audit is not None:
            await self._runtime.audit.record(
                AuditEvent(
                    kind="approval",
                    employee=self._employee(),
                    tenant=record.tenant,
                    subject=record.workflow_id,
                    actor=record.approver,
                    action=(
                        "approved"
                        if record.approved
                        else "expired"
                        if record.approver == "system"
                        else "denied"
                    ),
                    detail={
                        "task": record.task,
                        "call_id": record.call_id,
                        "tool": record.tool,
                        "comment": record.comment,
                    },
                    dedupe_key=f"{record.workflow_id}:{record.call_id}",
                    occurred_at=record.decided_at,
                )
            )
        self.approval_records.append(record)
        _logger.info("approval %s", json.dumps(record.model_dump(mode="json")))
        await self._write_back(
            "event",
            f"{'Approved' if record.approved else 'Denied'} {record.tool}"
            f" in task {record.task} by {record.approver}"
            + (f": {record.comment}" if record.comment else ""),
            {"workflow_id": record.workflow_id, "task": record.task},
        )

    def _employee(self) -> str:
        """The employee's manifest name (tests may bind a runtime without a
        resolved role)."""
        if self._runtime.resolved is None:
            return DEFAULT_AUTHOR
        return self._runtime.resolved.manifest.employee.name

    async def _audit_best_effort(self, event: AuditEvent) -> None:
        if self._runtime.audit is None:
            return
        try:
            await self._runtime.audit.record(event)
        except Exception:
            _logger.exception(
                "audit write failed kind=%s subject=%s", event.kind, event.subject
            )

    async def _write_back(
        self, kind: str, content: str, metadata: dict[str, str]
    ) -> None:
        """Best-effort write-back (D8): the substrate is the long-term
        record, but its unavailability must not fail the workflow. These
        entries are chassis-authored, so they carry employee provenance
        (D23) — unlike record_memory content, which is user-sourced."""
        if self._runtime.substrate is None:
            return
        author = (
            self._runtime.resolved.manifest.employee.name
            if self._runtime.resolved is not None
            else DEFAULT_AUTHOR
        )
        try:
            await self._runtime.substrate.record(
                kind, content, metadata, source="employee", author=author
            )
        except Exception:
            _logger.exception("memory write-back failed kind=%s", kind)

    @activity.defn
    async def notify_approvers(self, notice: ApproverNotice) -> None:
        """Push an escalation / approval notice to escalation.approvers_channel
        (D10). Raises on a failed send so Temporal retries; the workflow bounds
        the retries. With no push path (an http approvers channel is
        pull-based) it logs instead — the worker warned about that at start."""
        sender = self._runtime.approvers_sender
        if sender is None:
            _logger.info(
                "approver notice not pushed (no approvers push channel) key=%s: %s",
                notice.key,
                notice.reason,
            )
            return
        channel = (
            self._runtime.resolved.manifest.escalation.approvers_channel
            if self._runtime.resolved is not None
            else "unknown"
        )
        await sender.deliver(
            OutboundDelivery(
                key=notice.key,
                channel=channel,
                # Not a customer conversation: the sender is built with its own
                # destination and must not derive one from this.
                conversation_id=f"approvers-{notice.employee}",
                text=render_notice(notice),
                metadata={"audience": "approvers", "kind": notice.kind},
            )
        )

    @activity.defn
    async def deliver_reply(self, delivery: OutboundDelivery) -> None:
        sender = self._runtime.push_senders[delivery.channel]
        await sender.deliver(delivery)

    @property
    def all(self) -> list:
        return [
            self.run_guardrail,
            self.run_output_guardrail,
            self.compact_history,
            self.emit_outcome,
            self.record_escalation,
            self.record_approval,
            self.notify_approvers,
            self.deliver_reply,
        ]
