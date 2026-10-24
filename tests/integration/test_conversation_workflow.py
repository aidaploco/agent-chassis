"""End-to-end chassis tests against a real local Temporal dev server.

The model is a scripted FunctionModel (no API key); what's under test is the
durable shell: update-with-start, the D13 turn queue, routing over the
manifest graph, guardrail pre-flight, the D3 CAN rollover boundary, idle
timeout, and outcome emission — with the payload codec on (D9).
"""

import asyncio
import base64
import os
import uuid
from pathlib import Path

import pytest
import yaml
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.client import (
    WithStartWorkflowOperation,
    WorkflowExecutionStatus,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent_chassis.adapters.pydantic_ai import (
    PydanticAIAdapter,
    bind_workflow_agents,
    temporal_plugins,
)
from agent_chassis.config import ChassisSettings
from agent_chassis.contracts import ContentPart, MessageEnvelope
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.converter import build_data_converter
from agent_chassis.durable.models import (
    ApprovalSignal,
    ConversationParams,
    TaskParams,
    TurnReply,
)
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.manifest import load_manifest
from agent_chassis.packs import (
    CapabilityPack,
    GuardrailPack,
    GuardrailVerdict,
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
        async def publish_summary(summary: str) -> str:
            """Publish a summary (approval-gated in task tests)."""
            return f"published: {summary}"

        return [ToolSpec("publish_summary", publish_summary, "Publish a summary")]


class SupportPack(CapabilityPack):
    name = "support"

    def instructions(self, config: PackConfig) -> str:
        return "You handle support."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


GUARDRAIL_CONTEXTS: list[tuple[str, ...]] = []


class BlockWordGuardrail(GuardrailPack):
    name = "block_word"

    async def check(self, message, config: PackConfig, context=()) -> GuardrailVerdict:
        GUARDRAIL_CONTEXTS.append(tuple(context))
        if "FORBIDDEN" in message.text:
            return GuardrailVerdict(passed=False, reason="blocked word")
        return GuardrailVerdict(passed=True)


def _last_user_text(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


MODEL_CALLS: list[str] = []


def _current_instructions(messages: list[ModelMessage]) -> str:
    """Instructions of the agent running NOW — i.e. of the last request.

    With message_history, earlier requests carry the instructions of whichever
    agent ran back then; only the newest request has the current agent's.
    """
    for message in reversed(messages):
        if isinstance(message, ModelRequest) and message.instructions:
            return message.instructions
    return ""


def _script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministic stand-in for the LLM across all three agents."""
    instructions = _current_instructions(messages)
    text = _last_user_text(messages)
    MODEL_CALLS.append(text)

    if "double check" in text.lower():
        # Two sequential gated calls, then done — exercises back-to-back
        # approval pauses in one task.
        published = sum(
            1
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
            if type(p).__name__ == "ToolReturnPart"
        )
        if published == 0:
            return ModelResponse(
                parts=[ToolCallPart("publish_summary", {"summary": "first"})]
            )
        if published == 1:
            return ModelResponse(
                parts=[ToolCallPart("publish_summary", {"summary": "second"})]
            )
        return ModelResponse(parts=[TextPart("double done")])

    last_parts = messages[-1].parts
    returns = [p for p in last_parts if type(p).__name__ == "ToolReturnPart"]
    if returns:
        return ModelResponse(parts=[TextPart(f"task complete: {returns[0].content}")])
    if "daily check" in text.lower():
        return ModelResponse(
            parts=[ToolCallPart("publish_summary", {"summary": "all green"})]
        )

    is_supervisor = "You handle" not in instructions  # capability fragments absent
    if is_supervisor and "invoice" in text.lower():
        tool = next(t for t in info.output_tools if "route" in t.name.lower())
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool.name,
                    {"kind": "route", "target": "billing", "reason": "billing topic"},
                )
            ]
        )
    if "escalate me" in text.lower():
        tool = next(t for t in info.output_tools if "escalate" in t.name.lower())
        return ModelResponse(
            parts=[
                ToolCallPart(tool.name, {"kind": "escalate", "reason": "user asked"})
            ]
        )
    return ModelResponse(parts=[TextPart(f"echo[{len(messages)}]: {text}")])


FAKE_DELIVERIES: list = []


class FakeOutboundSender:
    """Records deliveries; dedupes by key like a real sender must (D15)."""

    async def deliver(self, delivery) -> None:
        if any(d.key == delivery.key for d in FAKE_DELIVERIES):
            return
        FAKE_DELIVERIES.append(delivery)


def _build_runtime(escalation: dict | None = None) -> RoleRuntime:
    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(SupportPack())
    registry.register(BlockWordGuardrail())

    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = [{"pack": "block_word", "config": {}}]
    data["channels"] = ["http", "slack"]
    data["tasks"] = [
        {"name": "daily_check", "agent": "billing", "prompt": "Run the daily check."},
        {"name": "double_check", "agent": "billing", "prompt": "Run the double check."},
    ]
    data["escalation"]["approval_required_tools"] = ["publish_summary"]
    if escalation:
        data["escalation"].update(escalation)
    path = FIXTURES / "_workflow_manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    try:
        manifest = load_manifest(path)
    finally:
        path.unlink()

    resolved = registry.resolve(manifest)
    adapter = PydanticAIAdapter(model=FunctionModel(_script))
    return RoleRuntime(
        resolved=resolved,
        adapter=adapter,
        handle=adapter.build(resolved),
        push_senders={"slack": FakeOutboundSender()},
    )


@pytest.fixture
def settings() -> ChassisSettings:
    return ChassisSettings(
        temporal_payload_codec_key=base64.b64encode(os.urandom(32)).decode()
    )


@pytest.fixture
async def env(settings):
    environment = await WorkflowEnvironment.start_local(
        data_converter=build_data_converter(settings),
        plugins=temporal_plugins(),
    )
    try:
        yield environment
    finally:
        await environment.shutdown()


def _chassis_fixture(escalation: dict | None = None):
    async def fixture(env):
        MODEL_CALLS.clear()
        FAKE_DELIVERIES.clear()
        GUARDRAIL_CONTEXTS.clear()
        runtime = _build_runtime(escalation)
        set_role_runtime(runtime)
        # Agents bind to ONE workflow class (PydanticAIPlugin registers
        # activities per class; both workflows share this worker).
        bind_workflow_agents(ConversationWorkflow, runtime.handle)
        activities = ChassisActivities(runtime)
        task_queue = f"tq-{uuid.uuid4()}"
        worker = Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ConversationWorkflow, TaskWorkflow],
            activities=activities.all,
        )
        async with worker:
            yield env.client, task_queue, activities

    return fixture


chassis = pytest.fixture(_chassis_fixture())
# Same chassis but with second-scale approval timers, for the expiry path.
fast_expiry_chassis = pytest.fixture(
    _chassis_fixture({"remind_after": "PT1S", "expire_after": "PT2S"})
)


def _envelope(conversation_id: str, text: str) -> MessageEnvelope:
    return MessageEnvelope(
        sender="u-1",
        tenant="acme",
        conversation_id=conversation_id,
        channel="http",
        parts=[ContentPart(text=text)],
    )


async def _send(
    client, task_queue: str, conversation_id: str, text: str, **params
) -> TurnReply:
    start_op = WithStartWorkflowOperation(
        ConversationWorkflow.run,
        ConversationParams(
            conversation_id=conversation_id,
            tenant="acme",
            channel="http",
            **params,
        ),
        id=f"conversation-{conversation_id}",
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return await client.execute_update_with_start_workflow(
        ConversationWorkflow.send_message,
        _envelope(conversation_id, text),
        start_workflow_operation=start_op,
    )


async def test_reply_routing_and_agent_stickiness(chassis):
    client, task_queue, _ = chassis
    conversation = str(uuid.uuid4())

    first = await _send(client, task_queue, conversation, "hello there")
    assert first.agent == "supervisor" and "hello there" in first.content

    second = await _send(client, task_queue, conversation, "I need my invoice")
    assert second.agent == "billing"  # routed supervisor → billing (D4)

    third = await _send(client, task_queue, conversation, "thanks!")
    assert (first.turn_number, second.turn_number, third.turn_number) == (1, 2, 3)
    detail = (
        f"content={third.content!r} events={[(e.agent, e.kind) for e in third.events]}"
    )
    assert third.agent == "billing", detail  # conversation sticks with billing


async def test_concurrent_updates_are_serialized_in_order(chassis):
    client, task_queue, _ = chassis
    conversation = str(uuid.uuid4())

    replies = await asyncio.gather(
        *(_send(client, task_queue, conversation, f"message {n}") for n in range(1, 4))
    )

    # Every message got its own turn (no interleaving, D13) …
    assert sorted(r.turn_number for r in replies) == [1, 2, 3]
    # … and each reply echoes its own message (no cross-talk).
    for n, reply in enumerate(replies, start=1):
        assert f"message {n}" in reply.content


async def test_rollover_boundary_preserves_conversation(chassis):
    client, task_queue, _ = chassis
    conversation = str(uuid.uuid4())

    echoes = []
    for n in range(1, 4):
        reply = await _send(
            client, task_queue, conversation, f"turn {n}", max_turns_per_run=1
        )
        echoes.append(reply)

    assert [r.turn_number for r in echoes] == [1, 2, 3]
    # History survived both rollovers: the model saw strictly more messages
    # each turn (echo[N] grows).
    sizes = [int(r.content.split("[")[1].split("]")[0]) for r in echoes]
    assert sizes[0] < sizes[1] < sizes[2]

    handle = client.get_workflow_handle(f"conversation-{conversation}")
    description = await handle.describe()
    assert description.status == WorkflowExecutionStatus.RUNNING


async def test_guardrail_trip_refuses_without_model_call(chassis):
    client, task_queue, _ = chassis
    conversation = str(uuid.uuid4())

    calls_before = len(MODEL_CALLS)
    reply = await _send(client, task_queue, conversation, "FORBIDDEN request")

    assert reply.guardrail_tripped == "block_word"
    assert "can't help" in reply.content
    assert len(MODEL_CALLS) == calls_before  # model never invoked


async def test_guardrails_receive_rolling_context(chassis):
    """The bug this guards against: a bare follow-up ("dana@example.com") judged
    in isolation looks off-topic; guardrails must see recent exchanges.
    Refused turns must NOT feed that context."""
    client, task_queue, _ = chassis
    conversation = str(uuid.uuid4())

    await _send(client, task_queue, conversation, "hello there")
    await _send(client, task_queue, conversation, "FORBIDDEN request")
    await _send(client, task_queue, conversation, "dana@example.com")

    first, second, third = GUARDRAIL_CONTEXTS[:3]
    assert first == ()  # turn 1 has nothing before it
    assert second[0] == "user: hello there"  # turn 2 sees the clean exchange
    assert third == second  # the refused turn was not recorded
    assert not any("FORBIDDEN" in line for line in third)

    # The window stays capped: after enough clean turns the oldest lines fall off.
    for text in ("one more thing", "and another", "ping"):
        await _send(client, task_queue, conversation, text)
    last = GUARDRAIL_CONTEXTS[-1]
    assert len(last) == 6
    assert last[0] == "user: dana@example.com"  # "hello there" exchange dropped


async def test_escalation_acknowledged_and_recorded(chassis):
    client, task_queue, activities = chassis
    conversation = str(uuid.uuid4())

    reply = await _send(client, task_queue, conversation, "please escalate me")

    assert reply.escalated is True
    assert "escalated" in reply.content
    assert activities.escalation_records
    assert activities.escalation_records[0].reason == "user asked"


async def test_idle_timeout_emits_outcome(chassis):
    client, task_queue, activities = chassis
    conversation = str(uuid.uuid4())

    await _send(client, task_queue, conversation, "hi", idle_timeout_seconds=1)

    handle = client.get_workflow_handle(f"conversation-{conversation}")
    await asyncio.wait_for(handle.result(), timeout=30)

    assert len(activities.emitted_outcomes) == 1
    outcome = activities.emitted_outcomes[0]
    assert outcome.resolution == "resolved"
    assert outcome.turns == 1
    assert outcome.role == "lana" and outcome.task_type == "conversation"


# -- TaskWorkflow (D6/D10/D11) ------------------------------------------------


async def _wait_for_pending(handle):
    while True:
        status = await handle.query(TaskWorkflow.status)
        if status.state == "waiting_on_human":
            return status
        await asyncio.sleep(0.1)


async def test_task_approval_round_trip(chassis):
    """Gated tool pauses the task (WAITING_ON_HUMAN); the approval signal
    resumes it; the decision is audit-logged BEFORE the tool runs (D11)."""
    client, task_queue, activities = chassis
    handle = await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=f"task-{uuid.uuid4()}",
        task_queue=task_queue,
    )

    status = await asyncio.wait_for(_wait_for_pending(handle), timeout=20)
    assert [item.tool for item in status.pending] == ["publish_summary"]
    assert "all green" in status.pending[0].args

    await handle.signal(
        TaskWorkflow.approve,
        ApprovalSignal(approved=True, approver="aida", comment="ship it"),
    )
    reply = await asyncio.wait_for(handle.result(), timeout=20)

    assert reply.approvals == 1 and reply.denials == 0 and reply.expired == 0
    assert "published: all green" in reply.content
    record = activities.approval_records[0]
    assert record.approved and record.approver == "aida"
    assert record.comment == "ship it" and record.tool == "publish_summary"
    outcome = activities.emitted_outcomes[-1]
    assert outcome.task_type == "daily_check"
    assert outcome.resolution == "escalated"  # the wait IS an escalation (D10)
    assert outcome.tool_calls == 1
    # The pause itself was escalation-recorded for the approvers' feed.
    assert any("approval required" in r.reason for r in activities.escalation_records)


async def test_task_approval_expiry_denies(fast_expiry_chassis):
    """Nobody answers: at expire_after the pending call is denied by
    'system' and the task completes without running the gated tool."""
    client, task_queue, activities = fast_expiry_chassis
    handle = await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=f"task-{uuid.uuid4()}",
        task_queue=task_queue,
    )

    reply = await asyncio.wait_for(handle.result(), timeout=30)

    assert reply.expired == 1 and reply.approvals == 0
    assert "Denied by system" in reply.content
    record = activities.approval_records[0]
    assert not record.approved and record.approver == "system"
    assert "expired" in record.comment


async def test_stale_or_unknown_approvals_do_not_leak(chassis):
    """A late/duplicate approval addressed to an already-decided call must
    not resolve a LATER pause — approvals only count against the pause the
    approver actually saw (buffered signals once auto-approved
    the next gated action)."""
    client, task_queue, activities = chassis
    handle = await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="double_check", tenant="acme"),
        id=f"task-{uuid.uuid4()}",
        task_queue=task_queue,
    )

    first = await asyncio.wait_for(_wait_for_pending(handle), timeout=20)
    assert "first" in first.pending[0].args
    first_call_id = first.pending[0].call_id
    await handle.signal(
        TaskWorkflow.approve,
        ApprovalSignal(approved=True, approver="aida"),
    )

    async def wait_for_second():
        while True:
            status = await handle.query(TaskWorkflow.status)
            if status.state == "waiting_on_human" and "second" in (
                status.pending[0].args if status.pending else ""
            ):
                return status
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait_for_second(), timeout=20)

    # A duplicate approval for the FIRST (already decided) call arrives now:
    # it must be ignored, leaving the second pause waiting.
    await handle.signal(
        TaskWorkflow.approve,
        ApprovalSignal(call_id=first_call_id, approved=True, approver="late"),
    )
    await asyncio.sleep(0.5)
    status = await handle.query(TaskWorkflow.status)
    assert status.state == "waiting_on_human"

    await handle.signal(
        TaskWorkflow.approve,
        ApprovalSignal(approved=True, approver="aida"),
    )
    reply = await asyncio.wait_for(handle.result(), timeout=20)
    assert reply.approvals == 2 and "double done" in reply.content
    # Both real decisions audited; the stale one produced no record.
    assert len(activities.approval_records) == 2
