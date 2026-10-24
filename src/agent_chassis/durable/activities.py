import json

from opentelemetry import trace
from temporalio import activity

from agent_chassis.channels.base import OutboundDelivery
from agent_chassis.contracts import (
    ContentPart,
    ConversationHistory,
    MessageEnvelope,
    OutcomeEvent,
)
from agent_chassis.durable.models import (
    ApprovalRecord,
    EscalationRecord,
    GuardrailCheck,
    GuardrailOutcome,
)
from agent_chassis.durable.runtime_context import RoleRuntime
from agent_chassis.memory.base import DEFAULT_AUTHOR
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)
_tracer = trace.get_tracer("agent_chassis.durable.activities")


class ChassisActivities:
    """Chassis-owned activities: guardrail pre-flight (D5), history
    compaction (D6a), outcome emission (D12), escalation records (D11).

    V1 emits outcomes and escalation records as structured logs; the
    Postgres audit/outcome store lands with the memory substrate phase.
    """

    def __init__(self, runtime: RoleRuntime) -> None:
        self._runtime = runtime
        self.emitted_outcomes: list[OutcomeEvent] = []
        self.escalation_records: list[EscalationRecord] = []
        self.approval_records: list[ApprovalRecord] = []

    @activity.defn
    async def run_guardrail(self, check: GuardrailCheck) -> GuardrailOutcome:
        resolved = next(
            g
            for g in self._runtime.resolved.guardrails
            if g.pack.name == check.guardrail
        )
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
        self.emitted_outcomes.append(event)
        _logger.info("outcome_event %s", json.dumps(event.model_dump(mode="json")))
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
        await self._write_back(
            "event",
            f"Escalated conversation {record.conversation_id}: {record.reason}",
            {"conversation_id": record.conversation_id, "agent": record.agent},
        )

    @activity.defn
    async def record_approval(self, record: ApprovalRecord) -> None:
        """D11: approval decisions share the audit stream with manifest
        overrides — same log family, same store."""
        self.approval_records.append(record)
        _logger.info("approval %s", json.dumps(record.model_dump(mode="json")))
        await self._write_back(
            "event",
            f"{'Approved' if record.approved else 'Denied'} {record.tool}"
            f" in task {record.task} by {record.approver}"
            + (f": {record.comment}" if record.comment else ""),
            {"workflow_id": record.workflow_id, "task": record.task},
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
    async def deliver_reply(self, delivery: OutboundDelivery) -> None:
        sender = self._runtime.push_senders[delivery.channel]
        await sender.deliver(delivery)

    @property
    def all(self) -> list:
        return [
            self.run_guardrail,
            self.compact_history,
            self.emit_outcome,
            self.record_escalation,
            self.record_approval,
            self.deliver_reply,
        ]
