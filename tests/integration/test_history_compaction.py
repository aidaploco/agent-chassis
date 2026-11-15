"""The D6a compaction trigger, end to end: history is compacted once it holds
a quarter more turns than the budget, back down to the budget."""

import uuid

import pytest
import yaml
from pydantic_ai.models.function import FunctionModel
from temporalio.worker import Worker

from agent_chassis.adapters.pydantic_ai import PydanticAIAdapter, bind_workflow_agents
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.manifest import RoleManifest
from agent_chassis.packs import PackRegistry

# Shared fixtures/fakes: same dev server, packs and scripted model.
from tests.integration.test_conversation_workflow import (  # noqa: F401
    FIXTURES,
    BillingPack,
    BlockWordGuardrail,
    SupportPack,
    _script,
    _send,
    env,
    settings,
)

MAX_TURNS = 4


@pytest.fixture
async def small_budget_chassis(env):  # noqa: F811
    registry = PackRegistry()
    for pack in (BillingPack(), SupportPack(), BlockWordGuardrail()):
        registry.register(pack)
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = [{"pack": "block_word", "config": {}}]
    data["runtime"]["history_budget"]["max_turns"] = MAX_TURNS
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


def _messages_seen(reply) -> int:
    """The scripted model echoes how many messages it was shown."""
    return int(reply.content.split("[")[1].split("]")[0])


async def test_history_stays_within_a_quarter_over_budget(small_budget_chassis):
    client, task_queue = small_budget_chassis
    conversation = str(uuid.uuid4())

    seen = []
    for n in range(1, 8):
        if n == 3:  # a refused turn adds no history and must not skew the count
            refused = await _send(client, task_queue, conversation, "FORBIDDEN")
            assert refused.guardrail_tripped == "block_word"
        seen.append(
            _messages_seen(await _send(client, task_queue, conversation, f"turn {n}"))
        )

    # Each clean turn adds a request and a response, so the model sees
    # 2 * (turns in history) + 1 messages. History grows to 5 turns
    # (budget 4 + slack 1), is compacted back to 4, and stays there.
    assert seen == [1, 3, 5, 7, 9, 9, 9]
