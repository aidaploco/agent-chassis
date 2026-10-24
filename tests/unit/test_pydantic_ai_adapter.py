from pathlib import Path

import pytest
import yaml
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_chassis.adapters.pydantic_ai import RUNTIME_ID, PydanticAIAdapter
from agent_chassis.contracts import (
    ApprovalDecision,
    ContentPart,
    ConversationHistory,
    HistoryBudget,
    MessageEnvelope,
    NeedsApproval,
    Reply,
    Route,
    TurnContext,
    TurnRequest,
)
from agent_chassis.manifest import load_manifest
from agent_chassis.packs import (
    CapabilityPack,
    PackConfig,
    PackRegistry,
    ToolSpec,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class BillingPack(CapabilityPack):
    name = "billing"

    def instructions(self, config: PackConfig) -> str:
        return "You handle billing."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def get_invoice(invoice_id: str) -> str:
            """Fetch one invoice."""
            return f"invoice {invoice_id}: 199 EUR"

        async def format_amount(cents: int) -> str:
            """Format cents as euros (pure)."""
            return f"{cents / 100:.2f} EUR"

        return [
            ToolSpec("get_invoice", get_invoice, "Fetch an invoice"),
            ToolSpec("format_amount", format_amount, "Format an amount", pure=True),
        ]


class SupportPack(CapabilityPack):
    name = "support"

    def instructions(self, config: PackConfig) -> str:
        return "You handle support."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


def _resolved(approval_required: list[str] | None = None):
    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(SupportPack())

    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["guardrails"] = []
    data["capabilities"][0]["config"] = {}
    if approval_required is not None:
        data["escalation"]["approval_required_tools"] = approval_required
    path = FIXTURES / "_adapter_manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    try:
        manifest = load_manifest(path)
    finally:
        path.unlink()
    return registry.resolve(manifest)


def _request(agent: str, text: str, history=None, turn=1, note=None) -> TurnRequest:
    return TurnRequest(
        agent=agent,
        message=MessageEnvelope(
            sender="u-1",
            tenant="acme",
            conversation_id="c-1",
            channel="http",
            parts=[ContentPart(text=text)],
        ),
        history=history or ConversationHistory(runtime=RUNTIME_ID),
        context=TurnContext(
            employee="lana", tenant="acme", conversation_id="c-1", turn_number=turn
        ),
        routing_note=note,
    )


def _adapter(fn) -> tuple[PydanticAIAdapter, object]:
    adapter = PydanticAIAdapter(model=FunctionModel(fn))
    return adapter, adapter.build(_resolved())


async def test_text_output_becomes_reply_with_events():
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Hello! How can I help?")])

    adapter, handle = _adapter(fn)
    result = await adapter.run_turn(handle, _request("supervisor", "hi"))

    assert result.directive == Reply(content="Hello! How can I help?")
    assert result.history.runtime == RUNTIME_ID and result.history.entries
    model_events = [e for e in result.events if e.kind == "model_request"]
    assert model_events and model_events[0].agent == "supervisor"


async def test_route_output_becomes_route_directive():
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = next(t for t in info.output_tools if "route" in t.name.lower())
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool.name,
                    {
                        "kind": "route",
                        "target": "billing",
                        "reason": "invoice question",
                    },
                )
            ]
        )

    adapter, handle = _adapter(fn)
    result = await adapter.run_turn(handle, _request("supervisor", "my invoice?"))

    assert isinstance(result.directive, Route)
    assert result.directive.target == "billing"
    # The directive rides an internal final_result_* output tool — it must
    # not surface as a tool_call event (it would pollute tools_called).
    assert not [e for e in result.events if e.kind == "tool_call"]


async def test_tool_call_round_trip_and_events():
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last_parts = messages[-1].parts
        if any(type(p).__name__ == "ToolReturnPart" for p in last_parts):
            return ModelResponse(parts=[TextPart("Your invoice is 199 EUR.")])
        return ModelResponse(
            parts=[ToolCallPart("get_invoice", {"invoice_id": "INV-1"})]
        )

    adapter, handle = _adapter(fn)
    result = await adapter.run_turn(handle, _request("billing", "show INV-1"))

    assert isinstance(result.directive, Reply)
    kinds = [(e.kind, e.name) for e in result.events]
    assert ("tool_call", "get_invoice") in kinds
    assert ("tool_output", "get_invoice") in kinds


def _task_request(agent: str, decisions=None, history=None, prompt=None, turn=1):
    return TurnRequest(
        agent=agent,
        message=None,
        prompt=prompt,
        decisions=decisions or [],
        history=history or ConversationHistory(runtime=RUNTIME_ID),
        context=TurnContext(
            employee="lana", tenant="acme", conversation_id="t-1", turn_number=turn
        ),
    )


def _approval_script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    last_parts = messages[-1].parts
    returns = [p for p in last_parts if type(p).__name__ == "ToolReturnPart"]
    if returns:
        return ModelResponse(parts=[TextPart(f"done: {returns[0].content}")])
    return ModelResponse(parts=[ToolCallPart("get_invoice", {"invoice_id": "INV-9"})])


async def test_gated_tool_pauses_and_approval_resumes():
    """D10 round trip: gated tool → NeedsApproval; approve → tool runs."""
    adapter = PydanticAIAdapter(model=FunctionModel(_approval_script))
    handle = adapter.build(_resolved(approval_required=["get_invoice"]))

    paused = await adapter.run_turn(
        handle, _task_request("billing", prompt="fetch INV-9")
    )
    assert isinstance(paused.directive, NeedsApproval)
    item = paused.directive.items[0]
    assert item.tool == "get_invoice" and "INV-9" in item.args
    # The pause is not counted as tool use — nothing ran.
    assert not [e for e in paused.events if e.kind == "tool_output"]

    from datetime import UTC, datetime

    resumed = await adapter.run_turn(
        handle,
        _task_request(
            "billing",
            history=paused.history,
            decisions=[
                ApprovalDecision(
                    call_id=item.call_id,
                    approved=True,
                    approver="aida",
                    comment="looks fine",
                    decided_at=datetime.now(UTC),
                )
            ],
            turn=2,
        ),
    )
    assert isinstance(resumed.directive, Reply)
    assert "invoice INV-9" in resumed.directive.content
    assert ("tool_output", "get_invoice") in [(e.kind, e.name) for e in resumed.events]


async def test_gated_tool_denial_feeds_comment_to_model():
    adapter = PydanticAIAdapter(model=FunctionModel(_approval_script))
    handle = adapter.build(_resolved(approval_required=["get_invoice"]))

    paused = await adapter.run_turn(
        handle, _task_request("billing", prompt="fetch INV-9")
    )
    assert isinstance(paused.directive, NeedsApproval)

    from datetime import UTC, datetime

    resumed = await adapter.run_turn(
        handle,
        _task_request(
            "billing",
            history=paused.history,
            decisions=[
                ApprovalDecision(
                    call_id=paused.directive.items[0].call_id,
                    approved=False,
                    approver="aida",
                    comment="not during quarter close",
                    decided_at=datetime.now(UTC),
                )
            ],
            turn=2,
        ),
    )
    assert isinstance(resumed.directive, Reply)
    assert "not during quarter close" in resumed.directive.content


def test_reserved_tool_name_prefix_rejected_at_build():
    """A pack tool named final_result_* would be silently filtered from tool
    events (the runtime reserves that prefix for structured outputs) — the
    adapter must refuse it loudly at build instead."""

    class BadPack(CapabilityPack):
        name = "support"  # reuse the manifest's declared capability slot

        def instructions(self, config: PackConfig) -> str:
            return "irrelevant"

        def tools(self, config: PackConfig) -> list[ToolSpec]:
            async def final_result_summary() -> str:
                """Innocent-looking tool with a reserved name."""
                return "x"

            return [ToolSpec("final_result_summary", final_result_summary, "bad")]

    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(BadPack())
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["guardrails"] = []
    data["capabilities"][0]["config"] = {}
    path = FIXTURES / "_adapter_bad_manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    try:
        manifest = load_manifest(path)
    finally:
        path.unlink()
    adapter = PydanticAIAdapter(model=FunctionModel(_approval_script))
    with pytest.raises(ValueError, match="reserved 'final_result' prefix"):
        adapter.build(registry.resolve(manifest))


async def test_ungated_agents_are_unaffected():
    """No approval_required_tools → no DeferredToolRequests in output_type,
    tool calls run straight through (the pre-D10 behavior)."""
    adapter = PydanticAIAdapter(model=FunctionModel(_approval_script))
    handle = adapter.build(_resolved())
    result = await adapter.run_turn(
        handle, _task_request("billing", prompt="fetch INV-9")
    )
    assert isinstance(result.directive, Reply)
    assert "invoice INV-9" in result.directive.content


async def test_history_round_trips_between_turns():
    seen: list[int] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(len(messages))
        return ModelResponse(parts=[TextPart("ok")])

    adapter, handle = _adapter(fn)
    first = await adapter.run_turn(handle, _request("supervisor", "one"))
    await adapter.run_turn(
        handle, _request("supervisor", "two", history=first.history, turn=2)
    )

    assert seen[1] > seen[0]  # second turn saw the first turn's messages


async def test_routing_note_reaches_instructions():
    captured: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(messages[0].instructions or "")
        return ModelResponse(parts=[TextPart("ok")])

    adapter, handle = _adapter(fn)
    await adapter.run_turn(
        handle, _request("billing", "hi", note="user asked about invoices")
    )

    assert "user asked about invoices" in captured[0]


async def test_leaf_agent_schema_and_naming():
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    adapter, handle = _adapter(fn)
    assert set(handle.agents) == {"supervisor", "billing", "support"}
    assert handle.agents["supervisor"].name == "lana_supervisor"


async def test_foreign_history_rejected():
    adapter, handle = _adapter(lambda m, i: ModelResponse(parts=[TextPart("x")]))
    foreign = ConversationHistory(runtime="openai_agents", entries=[{"role": "user"}])
    with pytest.raises(ValueError, match="belongs to runtime"):
        await adapter.run_turn(handle, _request("supervisor", "hi", history=foreign))


async def test_compact_history_keeps_recent_turns():
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    adapter, handle = _adapter(fn)
    history = ConversationHistory(runtime=RUNTIME_ID)
    for turn in range(1, 6):
        result = await adapter.run_turn(
            handle,
            _request("supervisor", f"message {turn}", history=history, turn=turn),
        )
        history = result.history

    compacted = await adapter.compact_history(
        handle, history, HistoryBudget(max_turns=2, summary_max_tokens=128)
    )

    assert compacted.summary and "3 earlier turns compacted" in compacted.summary
    assert len(compacted.entries) < len(history.entries)
    # Compacted history must still be loadable and runnable.
    result = await adapter.run_turn(
        adapter.build(_resolved()),
        _request("supervisor", "again", history=compacted, turn=6),
    )
    assert isinstance(result.directive, Reply)


async def test_under_budget_history_untouched():
    adapter, handle = _adapter(lambda m, i: ModelResponse(parts=[TextPart("x")]))
    result = await adapter.run_turn(handle, _request("supervisor", "hi"))

    compacted = await adapter.compact_history(
        handle, result.history, HistoryBudget(max_turns=40, summary_max_tokens=128)
    )
    assert compacted == result.history
