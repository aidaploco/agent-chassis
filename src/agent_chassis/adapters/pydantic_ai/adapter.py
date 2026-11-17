import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, TemporalDurability
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    Tool,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    FunctionToolset,
)

from agent_chassis.config import get_settings
from agent_chassis.contracts import (
    ApprovalDecision,
    ApprovalItem,
    ConversationHistory,
    Escalate,
    HistoryBudget,
    NeedsApproval,
    Reply,
    Route,
    TurnEvent,
    TurnRequest,
    TurnResult,
)
from agent_chassis.contracts.runtime import RuntimeHandle
from agent_chassis.packs.composition import compose_instructions
from agent_chassis.packs.registry import ResolvedRole

# pydantic-ai imports genai_prices.data (a ~16,000-line price table) lazily, the
# first time a workflow task runs a model request. Temporal gives a workflow task
# 2 seconds to yield; imported cold inside the sandbox in a container, that import
# alone exceeded it, so the first requests after every worker start logged
# TMPRL1101 deadlock errors and timed out. Importing it here puts it in
# sys.modules before any workflow task runs.
try:
    import genai_prices.data  # noqa: F401
except ImportError:  # a transitive dependency: nothing to warm if pydantic-ai drops it
    pass

RUNTIME_ID = "pydantic_ai"


def temporal_plugins() -> list[Any]:
    """Temporal client plugins this runtime needs (data converter guard,
    sandbox passthrough for the vendor SDK). The composition root passes
    these to Client.connect without importing the vendor itself."""
    return [PydanticAIPlugin()]


def bind_workflow_agents(workflow_class: type, handle: PydanticAIHandle) -> None:
    """Attach this handle's agents to a workflow class so PydanticAIPlugin
    auto-registers their activities on the worker."""
    workflow_class.__pydantic_ai_agents__ = list(handle.agents.values())  # type: ignore[attr-defined]


@dataclass
class RoleDeps:
    """Per-run dependencies injected into dynamic instructions."""

    routing_note: str | None = None
    history_summary: str | None = None


def _routing_note(ctx: RunContext[RoleDeps]) -> str:
    if ctx.deps.routing_note is None:
        return ""
    return f"Internal routing context (not from the user): {ctx.deps.routing_note}"


def _history_summary(ctx: RunContext[RoleDeps]) -> str:
    """Tell the agent its view of the conversation is truncated — otherwise
    compaction silently removes context and the agent cannot know to ask."""
    if not ctx.deps.history_summary:
        return ""
    return (
        "Earlier parts of this conversation are no longer visible to you:"
        f" {ctx.deps.history_summary}. If the user refers to something you"
        " cannot see, ask them to restate it rather than guessing."
    )


@dataclass
class PydanticAIHandle(RuntimeHandle):
    """Adapter-owned handle: one durable Agent per manifest agent.

    Agent names (``{employee}_{agent}``) and toolset ids (capability instance
    ids) feed Temporal activity names — frozen once deployed (D1/D7).
    """

    employee: str
    agents: dict[str, Agent[RoleDeps, Any]] = field(default_factory=dict)


class PydanticAIAdapter:
    """RuntimeAdapter implementation on pydantic-ai + TemporalDurability.

    Outside a Temporal workflow, the durability capability is transparent —
    which is why unit tests can drive this adapter without a server. Inside
    a workflow, model requests and non-pure tool calls become activities
    automatically.
    """

    def __init__(self, model: Model | str | None = None) -> None:
        # Test/eval override: one model for every agent (e.g. FunctionModel).
        self._model_override = model

    def build(self, resolved: ResolvedRole) -> PydanticAIHandle:
        manifest = resolved.manifest
        settings = get_settings()
        # Native pydantic-ai OTel instrumentation: genai spans for model
        # requests and tool calls, exported wherever setup_tracing points.
        Agent.instrument_all(True)
        handle = PydanticAIHandle(employee=manifest.employee.name)

        for agent_name, agent_spec in manifest.routing.agents.items():
            overrides = manifest.runtime.per_agent.get(agent_name)
            tier = (overrides and overrides.model_tier) or manifest.runtime.model_tier
            temperature = (
                overrides.temperature
                if overrides and overrides.temperature is not None
                else manifest.runtime.temperature
            )
            max_tokens = (
                overrides.max_output_tokens
                if overrides and overrides.max_output_tokens is not None
                else manifest.runtime.max_output_tokens
            )

            gated_tools = set(manifest.escalation.approval_required_tools)
            hosts_gated = False
            toolsets: list[AbstractToolset[RoleDeps]] = []
            for capability_id in agent_spec.capabilities:
                capability = resolved.capabilities[capability_id]
                specs = capability.pack.tools(capability.config)
                if not specs:
                    continue
                reserved = [s.name for s in specs if _is_directive_call(s.name)]
                if reserved:
                    # The runtime uses this prefix for its structured-output
                    # tools; a pack tool named this way would be silently
                    # filtered from tool events. Fail loudly at build instead.
                    raise ValueError(
                        f"pack {capability_id!r} tool names {reserved} use the"
                        " reserved 'final_result' prefix"
                    )
                toolset: AbstractToolset[RoleDeps] = FunctionToolset(
                    [
                        Tool(
                            spec.handler,
                            name=spec.name,
                            description=spec.description,
                            # D1 per-tool opt-out: a pure tool runs in the
                            # workflow instead of as an activity.
                            metadata={"temporal": False} if spec.pure else None,
                        )
                        for spec in specs
                    ],
                    id=capability_id,
                )
                gated_here = gated_tools & {spec.name for spec in specs}
                if gated_here:
                    # D10: gated tools raise ApprovalRequired instead of
                    # running; the run pauses as a DeferredToolRequests output
                    # and resumes with the humans' decisions. The wrapper sits
                    # OUTSIDE the leaf toolset, so Temporal activity naming
                    # (keyed on the leaf's id) is unaffected.
                    hosts_gated = True
                    toolset = ApprovalRequiredToolset(
                        toolset,
                        approval_required_func=(
                            lambda _ctx, tool_def, _args, _g=frozenset(gated_here): (
                                tool_def.name in _g
                            )
                        ),
                    )
                toolsets.append(toolset)

            # Route is only offered where the manifest declares outgoing edges;
            # the chassis still validates targets (D4) — this just keeps the
            # option out of leaf agents' schemas entirely.
            has_edges = any(s == agent_name for s, _ in manifest.routing.edges)
            output_type: list[Any] = (
                [str, Route, Escalate] if has_edges else [str, Escalate]
            )
            if hosts_gated:
                output_type.append(DeferredToolRequests)

            agent = Agent(
                self._model_override or settings.model_for_tier(tier),
                name=f"{manifest.employee.name}_{agent_name}",
                deps_type=RoleDeps,
                instructions=[
                    compose_instructions(resolved, agent_name),
                    _routing_note,
                    _history_summary,
                ],
                output_type=output_type,
                model_settings=ModelSettings(
                    temperature=temperature, max_tokens=max_tokens
                ),
                toolsets=toolsets,
                # Durability is a capability on a regular Agent (the
                # TemporalAgent wrapper is deprecated). Activity names derive
                # from the agent name and toolset ids exactly as before, so
                # workflows started under the wrapper replay unchanged.
                capabilities=[TemporalDurability()],
            )
            handle.agents[agent_name] = agent
        return handle

    async def run_turn(self, handle: RuntimeHandle, request: TurnRequest) -> TurnResult:
        assert isinstance(handle, PydanticAIHandle)
        agent = handle.agents[request.agent]

        user_prompt: str | None
        deferred_results: DeferredToolResults | None = None
        if request.message is not None:
            user_prompt = request.message.text
        elif request.decisions:
            # Resuming a WAITING_ON_HUMAN pause (D10): no new prompt — the
            # decisions settle the pending tool calls already in history.
            user_prompt = None
            deferred_results = _build_deferred_results(request.decisions)
        elif request.prompt is not None:
            user_prompt = request.prompt
        else:
            raise ValueError("TurnRequest needs a message, a prompt, or decisions")

        message_history = _load_messages(request.history)
        result = await agent.run(
            user_prompt,
            message_history=message_history or None,
            deferred_tool_results=deferred_results,
            deps=RoleDeps(
                routing_note=request.routing_note,
                history_summary=request.history.summary,
            ),
        )

        output = result.output
        if isinstance(output, DeferredToolRequests):
            if output.calls:  # we never build externally-executed tools
                raise ValueError(
                    f"unexpected external tool calls: {[c.tool_name for c in output.calls]}"
                )
            directive: Any = NeedsApproval(
                items=[
                    ApprovalItem(
                        call_id=call.tool_call_id,
                        tool=call.tool_name,
                        args=_approval_args(call.args),
                    )
                    for call in output.approvals
                ]
            )
        elif isinstance(output, str):
            directive = Reply(content=_collapse_repeated_text(result.response, output))
        else:
            directive = output
        return TurnResult(
            directive=directive,
            history=ConversationHistory(
                runtime=RUNTIME_ID,
                entries=ModelMessagesTypeAdapter.dump_python(
                    result.all_messages(), mode="json"
                ),
                summary=request.history.summary,
            ),
            events=_extract_events(request.agent, result.new_messages()),
        )

    async def compact_history(
        self,
        handle: RuntimeHandle,
        history: ConversationHistory,
        budget: HistoryBudget,
    ) -> ConversationHistory:
        """V1 compaction: keep the last ``max_turns`` user turns, fold the
        rest into a plain-text marker that ``run_turn`` shows the agent.
        Model-written summaries (using ``summary_max_tokens``) are a planned
        upgrade; that field is accepted but not used yet."""
        messages = _load_messages(history)
        boundaries = _turn_starts(messages)
        if len(boundaries) <= budget.max_turns:
            return history

        cut = boundaries[len(boundaries) - budget.max_turns]
        dropped = len(boundaries) - budget.max_turns
        # One running total, not one marker line per compaction: the summary
        # is carried in workflow state and must not grow with the conversation.
        already = sum(int(n) for n in _COMPACTED.findall(history.summary or ""))
        summary = f"[{already + dropped} earlier turns compacted]"
        return ConversationHistory(
            runtime=RUNTIME_ID,
            entries=ModelMessagesTypeAdapter.dump_python(messages[cut:], mode="json"),
            summary=summary,
        )


def _build_deferred_results(
    decisions: list[ApprovalDecision],
) -> DeferredToolResults:
    """Chassis decisions → vendor resume payload. Denials return the
    approver's comment to the model so it can explain, not just fail."""
    return DeferredToolResults(
        approvals={
            decision.call_id: (
                ToolApproved()
                if decision.approved
                else ToolDenied(
                    message=(
                        f"Denied by {decision.approver}: {decision.comment}"
                        if decision.comment
                        else f"Denied by {decision.approver}."
                    )
                )
            )
            for decision in decisions
        }
    )


def _load_messages(history: ConversationHistory) -> list[ModelMessage]:
    if history.runtime != RUNTIME_ID and history.entries:
        raise ValueError(f"history belongs to runtime {history.runtime!r}")
    if not history.entries:
        return []
    return ModelMessagesTypeAdapter.validate_python(history.entries)


def _collapse_repeated_text(response: ModelResponse, output: str) -> str:
    """The reply text, without a model's repeated copy of itself.

    Some models return the same sentence as two text parts in one response,
    and the runtime joins the parts, so a customer reads "What prompted the
    decision?What prompted the decision?". Observed live with gpt-5.4-mini on
    the cancellation flow (12 of 12 doubled before the billing prompt
    change, 5 of 12 after; none on tool-answer flows). Only the unambiguous
    case is collapsed — every text part identical — so genuinely different
    parts are still joined.
    """
    texts = [part.content for part in response.parts if isinstance(part, TextPart)]
    if len(texts) > 1 and len({text.strip() for text in texts}) == 1:
        return texts[0]
    return output


_COMPACTED = re.compile(r"\[(\d+) earlier turns compacted\]")


def _turn_starts(messages: list[ModelMessage]) -> list[int]:
    """Index of the request that opens each user turn.

    A routed turn (D4) re-sends the same user message to the target agent,
    so it holds several user-prompt requests. Counting each of them as a
    turn made ``max_turns`` mean "requests" for multi-agent roles — a
    supervisor-fronted role kept half the turns its manifest asked for. A
    user-prompt request that follows a route directive continues the turn.
    """
    starts: list[int] = []
    routed = False
    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            routed = any(
                isinstance(part, ToolCallPart) and _is_route_call(part.tool_name)
                for part in message.parts
            )
        elif any(isinstance(part, UserPromptPart) for part in message.parts):
            if not routed:
                starts.append(index)
            routed = False
    return starts


# What an approver is shown of a gated call's arguments. It used to be 500 characters,
# which cut a report body off mid-sentence and still asked a person to approve "it". The
# limit exists to bound the workflow payload and the approver notice, not to hide the
# call, so it is generous, and anything still cut says so in the text itself.
_MAX_APPROVAL_ARGS = 4000


def _approval_args(call_args: Any) -> str:
    text = str(call_args)
    if len(text) <= _MAX_APPROVAL_ARGS:
        return text
    omitted = len(text) - _MAX_APPROVAL_ARGS
    return (
        f"{text[:_MAX_APPROVAL_ARGS]}… [truncated: {omitted} more characters not shown]"
    )


def _is_route_call(tool_name: str) -> bool:
    return _is_directive_call(tool_name) and "route" in tool_name.lower()


def _is_directive_call(tool_name: str) -> bool:
    """pydantic-ai delivers structured outputs (our TurnDirective union) as
    calls to internal output tools named ``final_result`` /
    ``final_result_<Member>`` (its DEFAULT_OUTPUT_TOOL_NAME). Those are the
    reply mechanism, not tool use — counting them would pollute
    ``tools_called``, the outcome counters, and eval tool grading."""
    return tool_name.startswith("final_result")


def _extract_events(agent_name: str, messages: list[ModelMessage]) -> list[TurnEvent]:
    events: list[TurnEvent] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            events.append(
                TurnEvent(
                    kind="model_request",
                    agent=agent_name,
                    name="model",
                    model=message.model_name,
                    tokens_in=message.usage.input_tokens,
                    tokens_out=message.usage.output_tokens,
                )
            )
            events.extend(
                TurnEvent(
                    kind="tool_call",
                    agent=agent_name,
                    name=part.tool_name,
                    payload={"args": str(part.args)[:500]},
                )
                for part in message.parts
                if isinstance(part, ToolCallPart)
                and not _is_directive_call(part.tool_name)
            )
        elif isinstance(message, ModelRequest):
            events.extend(
                TurnEvent(
                    kind="tool_output",
                    agent=agent_name,
                    name=part.tool_name,
                    payload={"output": str(part.content)[:500]},
                )
                for part in message.parts
                if isinstance(part, ToolReturnPart)
                and not _is_directive_call(part.tool_name)
            )
    return events
