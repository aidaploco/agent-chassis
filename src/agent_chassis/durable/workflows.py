import asyncio
from dataclasses import dataclass
from datetime import UTC, timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from opentelemetry import context as otel_context

    from agent_chassis.channels.base import OutboundDelivery
    from agent_chassis.contracts import (
        ApprovalDecision,
        ConversationHistory,
        Escalate,
        MessageEnvelope,
        NeedsApproval,
        OutcomeEvent,
        Reply,
        Resolution,
        Route,
        TurnContext,
        TurnRequest,
    )
    from agent_chassis.durable.models import (
        ApprovalRecord,
        ApprovalSignal,
        ConversationParams,
        ConversationState,
        EscalationRecord,
        GuardrailCheck,
        GuardrailOutcome,
        TaskParams,
        TaskReply,
        TaskStatus,
        TurnReply,
    )
    from agent_chassis.durable.runtime_context import get_role_runtime

_FALLBACK_REPLY = (
    "I wasn't able to complete that request. Please try rephrasing, or ask me"
    " something else."
)
_REFUSAL_REPLY = "I can't help with that request."
_ESCALATION_ACK = (
    "I've escalated this to a human teammate — they'll follow up with you."
)

# Guardrail context window: last N transcript lines (N/2 exchanges). Small on
# purpose — enough to resolve "bare reply to the previous question", not a
# second history store.
_TRANSCRIPT_WINDOW = 6

_GUARDRAIL_ACTIVITY_TIMEOUT = timedelta(seconds=30)
_EMIT_ACTIVITY_TIMEOUT = timedelta(seconds=10)
_COMPACT_ACTIVITY_TIMEOUT = timedelta(seconds=120)


def _absorb_usage_events(state: "ConversationState", events) -> None:
    """Fold a turn's TurnEvents into the state's usage counters — shared by
    both workflow types (identical semantics, identical field writes)."""
    for event in events:
        if event.kind == "tool_call":
            state.tool_calls += 1
        if event.kind == "model_request":
            state.tokens_in += event.tokens_in or 0
            state.tokens_out += event.tokens_out or 0
            if event.model and event.model not in state.models_used:
                state.models_used.append(event.model)


def _outcome_event(
    *,
    role: str,
    tenant: str,
    task_type: str,
    conversation_id: str,
    state: "ConversationState",
    resolution: "Resolution",
    turns: int,
) -> "OutcomeEvent":
    """One OutcomeEvent (D12) from workflow state — shared by both workflow
    types; only resolution policy and identity fields differ per caller."""
    ended = workflow.now().astimezone(UTC)
    return OutcomeEvent(
        role=role,
        tenant=tenant,
        task_type=task_type,
        conversation_id=conversation_id,
        workflow_run_id=workflow.info().run_id,
        resolution=resolution,
        turns=turns,
        tool_calls=state.tool_calls,
        wall_time_ms=int((ended - state.started_at).total_seconds() * 1000),
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
        escalations=state.escalations,
        guardrail_trips=state.guardrail_trips,
        models_used=state.models_used,
        started_at=state.started_at,
        ended_at=ended,
    )


@dataclass
class _PendingTurn:
    """One queued inbound message (D13): FIFO-processed, awaited by its
    update handler."""

    envelope: MessageEnvelope
    reply: TurnReply | None = None
    error: str | None = None
    done: bool = False
    # Trace context captured in the update handler (where the interceptor
    # attached the caller's context). The queue decouples handling from
    # processing, so without this every turn's activity spans would land in
    # whichever trace was attached last.
    trace_context: object | None = None


@workflow.defn
class ConversationWorkflow:
    """Durable conversation shell — vendor-free (D2).

    Messages arrive as updates (D3) and are serialized through an in-workflow
    FIFO turn queue (D13). Each turn runs guardrail pre-flight activities
    (D5), then the routing loop over the manifest graph (D4); the runtime
    adapter's run_turn expands into vendor-managed activities inside this
    workflow. Rollover: queue drained + handlers finished before
    continue-as-new; state carried in params (D6a). Escalation here records
    and acknowledges (D10); the durable-approval wait ships with TaskWorkflow.
    """

    @workflow.init
    def __init__(self, params: ConversationParams) -> None:
        self._params = params
        runtime = get_role_runtime()
        manifest = runtime.resolved.manifest
        self._runtime = runtime
        self._manifest = manifest
        state = params.resume or ConversationState(
            history=ConversationHistory(runtime=""),
            current_agent=manifest.routing.entry,
            started_at=workflow.now().astimezone(UTC),
        )
        self._state = state
        self._queue: list[_PendingTurn] = []
        self._turns_this_run = 0
        self._last_activity_at = workflow.now()

    @workflow.run
    async def run(self, params: ConversationParams) -> None:
        while True:
            if self._should_roll_over() and not self._queue:
                await workflow.wait_condition(workflow.all_handlers_finished)
                if not self._queue:  # drained (D13): nothing arrived meanwhile
                    workflow.continue_as_new(
                        params.model_copy(update={"resume": self._state}, deep=True)
                    )

            if self._queue:
                item = self._queue[0]
                token = (
                    otel_context.attach(item.trace_context)
                    if item.trace_context is not None
                    else None
                )
                try:
                    item.reply = await self._process(item.envelope)
                except Exception as exc:  # surfaced to the update handler
                    item.error = str(exc)
                finally:
                    if token is not None:
                        otel_context.detach(token)
                    item.done = True
                    self._queue.pop(0)
                    self._last_activity_at = workflow.now()
                if item.reply is not None:
                    await self._maybe_push_reply(item.envelope, item.reply)
                continue

            remaining = self._idle_remaining()
            if remaining <= 0:
                await workflow.wait_condition(workflow.all_handlers_finished)
                if not self._queue and self._idle_remaining() <= 0:
                    await self._emit_outcome()
                    return
                continue

            try:
                await workflow.wait_condition(
                    lambda: bool(self._queue) or self._should_roll_over(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                pass

    @workflow.update
    async def send_message(self, envelope: MessageEnvelope) -> TurnReply:
        """Enqueue one inbound message and await its turn's completion."""
        item = _PendingTurn(envelope=envelope, trace_context=otel_context.get_current())
        self._queue.append(item)
        await workflow.wait_condition(lambda: item.done)
        if item.error is not None:
            raise ApplicationError(f"turn failed: {item.error}")
        assert item.reply is not None
        return item.reply

    # -- turn processing ----------------------------------------------------

    async def _process(self, envelope: MessageEnvelope) -> TurnReply:
        self._state.turn_number += 1
        self._turns_this_run += 1
        turn_number = self._state.turn_number

        tripped = await self._run_guardrails(envelope)
        if tripped is not None:
            self._state.guardrail_trips += 1
            return TurnReply(
                content=_REFUSAL_REPLY,
                agent=self._state.current_agent,
                turn_number=turn_number,
                guardrail_tripped=tripped.guardrail,
            )

        reply = await self._routing_loop(envelope, turn_number)
        # Only clean turns feed future guardrail context — refused messages
        # must not smuggle their content back in through the side door.
        self._state.transcript = (
            self._state.transcript
            + [f"user: {envelope.text}", f"assistant: {reply.content}"]
        )[-_TRANSCRIPT_WINDOW:]
        await self._maybe_compact()
        return reply

    async def _run_guardrails(
        self, envelope: MessageEnvelope
    ) -> GuardrailOutcome | None:
        for resolved in self._runtime.resolved.guardrails:
            outcome: GuardrailOutcome = await workflow.execute_activity(
                "run_guardrail",
                GuardrailCheck(
                    guardrail=resolved.pack.name,
                    sender=envelope.sender,
                    tenant=envelope.tenant,
                    conversation_id=envelope.conversation_id,
                    channel=envelope.channel,
                    text=envelope.text,
                    context=self._state.transcript,
                ),
                result_type=GuardrailOutcome,
                start_to_close_timeout=_GUARDRAIL_ACTIVITY_TIMEOUT,
            )
            if not outcome.passed:
                return outcome
        return None

    async def _routing_loop(
        self, envelope: MessageEnvelope, turn_number: int
    ) -> TurnReply:
        manifest = self._manifest
        runtime = self._runtime
        agent = self._state.current_agent
        note: str | None = None
        events = []

        for _hop in range(manifest.routing.max_hops_per_message + 1):
            history_before = self._state.history
            result = await runtime.adapter.run_turn(
                runtime.handle,
                TurnRequest(
                    agent=agent,
                    message=envelope,
                    history=self._state.history,
                    context=TurnContext(
                        employee=manifest.employee.name,
                        tenant=envelope.tenant,
                        conversation_id=envelope.conversation_id,
                        turn_number=turn_number,
                    ),
                    routing_note=note,
                ),
            )
            self._state.history = result.history
            events.extend(result.events)
            _absorb_usage_events(self._state, result.events)
            directive = result.directive

            if isinstance(directive, Reply):
                self._state.current_agent = agent
                return TurnReply(
                    content=directive.content,
                    agent=agent,
                    turn_number=turn_number,
                    events=events,
                )

            if isinstance(directive, NeedsApproval):
                # Conversations don't hold the WAITING_ON_HUMAN pause in V1 —
                # that's TaskWorkflow's job (D10). A gated tool reached from a
                # conversation escalates instead, and the paused history is
                # discarded so its unresolved tool calls can't poison the
                # next turn.
                self._state.history = history_before
                directive = Escalate(
                    reason="approval required for: "
                    + ", ".join(item.tool for item in directive.items),
                    payload={
                        "pending": [item.model_dump() for item in directive.items]
                    },
                )

            if isinstance(directive, Escalate):
                self._state.current_agent = agent
                self._state.escalations += 1
                await workflow.execute_activity(
                    "record_escalation",
                    EscalationRecord(
                        conversation_id=envelope.conversation_id,
                        tenant=envelope.tenant,
                        agent=agent,
                        reason=directive.reason,
                        payload=directive.payload,
                        occurred_at=workflow.now().astimezone(UTC),
                    ),
                    start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
                )
                return TurnReply(
                    content=_ESCALATION_ACK,
                    agent=agent,
                    turn_number=turn_number,
                    escalated=True,
                    events=events,
                )

            assert isinstance(directive, Route)
            edge = (agent, directive.target)
            if edge not in {tuple(e) for e in manifest.routing.edges}:
                workflow.logger.error("illegal route %s -> %s", *edge)
                # The conversation stays with the agent that ran (it produced
                # a bad directive, but it owns the conversation).
                self._state.current_agent = agent
                return TurnReply(
                    content=_FALLBACK_REPLY,
                    agent=agent,
                    turn_number=turn_number,
                    events=events,
                )
            note = (
                directive.reason
                if not directive.note
                else (f"{directive.reason} — {directive.note}")
            )
            agent = directive.target

        workflow.logger.error("hop budget exhausted for turn %s", turn_number)
        self._state.current_agent = agent
        return TurnReply(
            content=_FALLBACK_REPLY,
            agent=agent,
            turn_number=turn_number,
            events=events,
        )

    async def _maybe_push_reply(
        self, envelope: MessageEnvelope, reply: TurnReply
    ) -> None:
        """Push channels get their reply via an outbound activity (D15);
        sync channels already received it through the update result."""
        if envelope.channel not in self._runtime.push_senders:
            return
        await workflow.execute_activity(
            "deliver_reply",
            OutboundDelivery(
                key=f"{envelope.conversation_id}:turn:{reply.turn_number}",
                channel=envelope.channel,
                conversation_id=envelope.conversation_id,
                text=reply.content,
            ),
            start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
        )

    # -- policy helpers ------------------------------------------------------

    async def _maybe_compact(self) -> None:
        budget = self._manifest.runtime.history_budget
        if (
            self._state.turn_number > 0
            and self._state.turn_number % budget.max_turns == 0
        ):
            self._state.history = await workflow.execute_activity(
                "compact_history",
                self._state.history,
                result_type=ConversationHistory,
                start_to_close_timeout=_COMPACT_ACTIVITY_TIMEOUT,
            )

    def _should_roll_over(self) -> bool:
        if workflow.info().is_continue_as_new_suggested():
            return True
        max_turns = self._params.max_turns_per_run
        return max_turns is not None and self._turns_this_run >= max_turns

    def _idle_remaining(self) -> float:
        idle_for = (workflow.now() - self._last_activity_at).total_seconds()
        return self._params.idle_timeout_seconds - idle_for

    async def _emit_outcome(self) -> None:
        state = self._state
        if state.escalations:
            resolution = Resolution.ESCALATED
        elif state.turn_number:
            resolution = Resolution.RESOLVED
        else:
            resolution = Resolution.ABANDONED
        await workflow.execute_activity(
            "emit_outcome",
            _outcome_event(
                role=self._manifest.employee.name,
                tenant=self._params.tenant,
                task_type="conversation",
                conversation_id=self._params.conversation_id,
                state=state,
                resolution=resolution,
                turns=state.turn_number,
            ),
            start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
        )


@workflow.defn
class TaskWorkflow:
    """Scheduled/one-shot work with durable human approval (D6/D10).

    Run-to-completion: one manifest task runs on its agent; when the agent
    calls an approval-gated tool the workflow enters WAITING_ON_HUMAN —
    approvers decide via the ``approve`` signal, reminders fire every
    ``remind_after``, and at ``expire_after`` undecided calls are denied.
    Every decision is audit-logged via activity BEFORE the run resumes (D11).
    """

    @workflow.init
    def __init__(self, params: TaskParams) -> None:
        self._params = params
        runtime = get_role_runtime()
        self._runtime = runtime
        self._manifest = runtime.resolved.manifest
        spec = next((t for t in self._manifest.tasks if t.name == params.task), None)
        if spec is None:
            raise ApplicationError(f"unknown task {params.task!r}", non_retryable=True)
        self._spec = spec
        self._signals: list[ApprovalSignal] = []
        self._pending: list = []  # ApprovalItem while WAITING_ON_HUMAN
        self._state = ConversationState(
            history=ConversationHistory(runtime=""),
            current_agent=spec.agent,
            started_at=workflow.now().astimezone(UTC),
        )

    @workflow.run
    async def run(self, params: TaskParams) -> TaskReply:
        spec = self._spec
        runs = 0
        approvals = denials = expired = 0

        result = await self._run_task_turn(
            prompt=params.prompt_override or spec.prompt, decisions=[], runs=runs
        )
        runs += 1

        while isinstance(result.directive, NeedsApproval):
            self._state.escalations += 1
            items = result.directive.items
            self._pending = items
            await workflow.execute_activity(
                "record_escalation",
                EscalationRecord(
                    conversation_id=workflow.info().workflow_id,
                    tenant=params.tenant,
                    agent=spec.agent,
                    reason="approval required: "
                    + ", ".join(item.tool for item in items),
                    payload={"pending": [item.model_dump() for item in items]},
                    occurred_at=workflow.now().astimezone(UTC),
                ),
                start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
            )
            decisions = await self._await_decisions(items)
            self._pending = []
            for decision in decisions:
                approvals += decision.approved
                denials += (not decision.approved) and decision.approver != "system"
                expired += decision.approver == "system"
                # D11: the audit entry lands before the decision takes effect.
                await workflow.execute_activity(
                    "record_approval",
                    ApprovalRecord(
                        workflow_id=workflow.info().workflow_id,
                        task=spec.name,
                        tenant=params.tenant,
                        call_id=decision.call_id,
                        tool=next(
                            i.tool for i in items if i.call_id == decision.call_id
                        ),
                        approved=decision.approved,
                        approver=decision.approver,
                        comment=decision.comment,
                        decided_at=decision.decided_at,
                    ),
                    start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
                )
            result = await self._run_task_turn(
                prompt=None, decisions=decisions, runs=runs
            )
            runs += 1

        directive = result.directive
        if isinstance(directive, Reply):
            content, escalated = directive.content, False
        elif isinstance(directive, Escalate):
            self._state.escalations += 1
            await workflow.execute_activity(
                "record_escalation",
                EscalationRecord(
                    conversation_id=workflow.info().workflow_id,
                    tenant=params.tenant,
                    agent=spec.agent,
                    reason=directive.reason,
                    payload=directive.payload,
                    occurred_at=workflow.now().astimezone(UTC),
                ),
                start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
            )
            content, escalated = _ESCALATION_ACK, True
        else:  # Route from a task agent has no meaning (D6)
            workflow.logger.error("task agent produced a route directive")
            content, escalated = _FALLBACK_REPLY, False

        await self._emit_task_outcome(runs)
        return TaskReply(
            task=spec.name,
            content=content,
            agent=spec.agent,
            escalated=escalated,
            approvals=approvals,
            denials=denials,
            expired=expired,
            events=result.events,
        )

    @workflow.signal
    def approve(self, signal: ApprovalSignal) -> None:
        """Approver decision (D10). ``call_id`` None decides all pending.

        Signals that arrive while nothing is pending are DROPPED (with a
        warning), never buffered: a duplicate or late approval must not sit
        around and silently auto-approve the NEXT gated action — an approval
        is only meaningful against the pause the approver actually saw.
        """
        if not self._pending:
            workflow.logger.warning(
                "approval from %r ignored — no pending approvals", signal.approver
            )
            return
        self._signals.append(signal)

    @workflow.query
    def status(self) -> TaskStatus:
        return TaskStatus(
            task=self._spec.name,
            state="waiting_on_human" if self._pending else "running",
            pending=list(self._pending),
        )

    async def _run_task_turn(self, prompt, decisions, runs):
        result = await self._runtime.adapter.run_turn(
            self._runtime.handle,
            TurnRequest(
                agent=self._spec.agent,
                message=None,
                prompt=prompt,
                decisions=decisions,
                history=self._state.history,
                context=TurnContext(
                    employee=self._manifest.employee.name,
                    tenant=self._params.tenant,
                    conversation_id=workflow.info().workflow_id,
                    turn_number=runs + 1,
                ),
            ),
        )
        self._state.history = result.history
        self._state.turn_number = runs + 1
        _absorb_usage_events(self._state, result.events)
        return result

    async def _await_decisions(self, items) -> list["ApprovalDecision"]:
        """WAITING_ON_HUMAN (D10): durable wait with reminder and expiry
        timers. Undecided calls are denied at expiry (approver 'system')."""
        escalation = self._manifest.escalation
        started = workflow.now()
        deadline = started + escalation.expire_after
        next_reminder = started + escalation.remind_after
        undecided = {item.call_id for item in items}
        decided: dict[str, ApprovalDecision] = {}

        while undecided and workflow.now() < deadline:
            timeout = (min(next_reminder, deadline) - workflow.now()).total_seconds()
            try:
                await workflow.wait_condition(
                    lambda: bool(self._signals), timeout=max(timeout, 0.1)
                )
            except asyncio.TimeoutError:
                if workflow.now() >= next_reminder:
                    # Reminders go where the original pause went — the
                    # escalation feed — not just a worker log line nobody
                    # watches (D10: reminders exist to nudge the approver).
                    await workflow.execute_activity(
                        "record_escalation",
                        EscalationRecord(
                            conversation_id=workflow.info().workflow_id,
                            tenant=self._params.tenant,
                            agent=self._spec.agent,
                            reason="approval reminder — still waiting on: "
                            + ", ".join(
                                i.tool for i in items if i.call_id in undecided
                            ),
                            payload={"pending_call_ids": sorted(undecided)},
                            occurred_at=workflow.now().astimezone(UTC),
                        ),
                        start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
                    )
                    next_reminder = workflow.now() + escalation.remind_after
                continue
            for signal in self._signals:
                targets = (
                    [signal.call_id]
                    if signal.call_id is not None
                    else sorted(undecided)
                )
                for call_id in targets:
                    if call_id not in undecided:
                        workflow.logger.warning(
                            "approval from %r targets unknown or already-decided"
                            " call_id %r — ignored",
                            signal.approver,
                            call_id,
                        )
                        continue
                    undecided.discard(call_id)
                    decided[call_id] = ApprovalDecision(
                        call_id=call_id,
                        approved=signal.approved,
                        approver=signal.approver,
                        comment=signal.comment,
                        decided_at=workflow.now().astimezone(UTC),
                    )
            self._signals.clear()

        for call_id in sorted(undecided):
            decided[call_id] = ApprovalDecision(
                call_id=call_id,
                approved=False,
                approver="system",
                comment=f"approval expired after {escalation.expire_after}",
                decided_at=workflow.now().astimezone(UTC),
            )
        return [decided[item.call_id] for item in items]

    async def _emit_task_outcome(self, runs: int) -> None:
        await workflow.execute_activity(
            "emit_outcome",
            _outcome_event(
                role=self._manifest.employee.name,
                tenant=self._params.tenant,
                task_type=self._spec.name,
                conversation_id=workflow.info().workflow_id,
                state=self._state,
                resolution=(
                    Resolution.ESCALATED
                    if self._state.escalations
                    else Resolution.RESOLVED
                ),
                turns=runs,
            ),
            start_to_close_timeout=_EMIT_ACTIVITY_TIMEOUT,
        )
