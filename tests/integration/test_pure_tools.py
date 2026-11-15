"""D1's per-tool opt-out under a real workflow: a ``pure=True`` tool runs in
the workflow, an ordinary tool becomes a Temporal activity. Read from the
workflow's own event history, so it checks what Temporal actually scheduled."""

import uuid

import pytest
import yaml
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.worker import Worker

from agent_chassis.adapters.pydantic_ai import PydanticAIAdapter, bind_workflow_agents
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.manifest import RoleManifest
from agent_chassis.packs import CapabilityPack, PackConfig, PackRegistry, ToolSpec

# Shared fixtures: the same dev server (payload codec on) and a fixture pack.
from tests.integration.test_conversation_workflow import (  # noqa: F401
    FIXTURES,
    SupportPack,
    _send,
    env,
    settings,
)


class ToolsPack(CapabilityPack):
    name = "billing"

    def instructions(self, config: PackConfig) -> str:
        return "You handle billing."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def shout(text: str) -> str:
            """Upper-case text (pure: no I/O)."""
            return text.upper()

        async def lookup(key: str) -> str:
            """Look something up (does I/O, so it is an activity)."""
            return f"value-of-{key}"

        return [
            ToolSpec("shout", shout, "Upper-case text", pure=True),
            ToolSpec("lookup", lookup, "Look something up"),
        ]


def _script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = sum(
        1
        for m in messages
        for p in getattr(m, "parts", [])
        if type(p).__name__ == "ToolReturnPart"
    )
    if returns == 0:
        return ModelResponse(parts=[ToolCallPart("shout", {"text": "hello"})])
    if returns == 1:
        return ModelResponse(parts=[ToolCallPart("lookup", {"key": "k"})])
    return ModelResponse(parts=[TextPart("done")])


@pytest.fixture
async def chassis(env):  # noqa: F811
    registry = PackRegistry()
    registry.register(ToolsPack())
    registry.register(SupportPack())
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = []
    # Billing handles the message directly: no routing hop in this test.
    data["routing"]["entry"] = "billing"
    resolved = registry.resolve(RoleManifest.model_validate(data))
    adapter = PydanticAIAdapter(model=FunctionModel(_script))
    runtime = RoleRuntime(
        resolved=resolved, adapter=adapter, handle=adapter.build(resolved)
    )
    set_role_runtime(runtime)
    bind_workflow_agents(ConversationWorkflow, runtime.handle)
    task_queue = f"tq-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ConversationWorkflow, TaskWorkflow],
        activities=ChassisActivities(runtime).all,
    ):
        yield env.client, task_queue


async def test_pure_tool_runs_in_the_workflow_and_others_are_activities(chassis):
    client, task_queue = chassis
    conversation = str(uuid.uuid4())

    reply = await _send(client, task_queue, conversation, "do both")
    assert reply.content == "done"
    assert [e.name for e in reply.events if e.kind == "tool_call"] == [
        "shout",
        "lookup",
    ]

    history = await client.get_workflow_handle(
        f"conversation-{conversation}"
    ).fetch_history()
    scheduled = [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]
    # Activity names are per toolset ("...toolset__billing__call_tool"), not
    # per tool, so count them: two tools were called but only one was
    # scheduled as an activity — the other ran inside the workflow.
    tool_activities = [name for name in scheduled if name.endswith("__call_tool")]
    model_activities = [name for name in scheduled if name.endswith("__model_request")]
    assert len(tool_activities) == 1, scheduled
    assert len(model_activities) == 3, scheduled  # shout, lookup, final answer
