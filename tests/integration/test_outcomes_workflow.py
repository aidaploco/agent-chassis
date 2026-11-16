"""Outcome rows from the real workflows (D12), against the Temporal dev server.

The sink is an in-memory fake with the same idempotency contract as Postgres.
What is under test is the workflow behaviour: one outcome per finished
conversation or task, retried through a transient outage, and never allowed to
hold a workflow open once the retries are spent.
"""

import asyncio
import uuid
from datetime import timedelta

from temporalio.client import WorkflowExecutionStatus
from temporalio.common import RetryPolicy

from agent_chassis.contracts import OutcomeEvent
from agent_chassis.durable.models import ApprovalSignal, TaskParams
from agent_chassis.durable.workflows import TaskWorkflow

# Shared fixtures/fakes: same dev server, packs and scripted model.
from tests.integration.test_conversation_workflow import (  # noqa: F401
    _chassis_fixture,
    _send,
    _wait_for_pending,
    env,
    settings,
)

_FAST_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=100),
    backoff_coefficient=1.0,
    maximum_attempts=3,
)


class FakeOutcomes:
    def __init__(self, *, fail_first: int = 0, always_fail: bool = False) -> None:
        self.events: list[OutcomeEvent] = []
        self.attempts = 0
        self._fail_first = fail_first
        self._always_fail = always_fail

    async def record(self, event: OutcomeEvent) -> bool:
        self.attempts += 1
        if self._always_fail or self.attempts <= self._fail_first:
            raise ConnectionError("outcome store unreachable")
        if any(
            (e.role, e.workflow_run_id) == (event.role, event.workflow_run_id)
            for e in self.events
        ):
            return False
        self.events.append(event)
        return True


async def _chassis(env, sink, escalation=None):  # noqa: F811
    gen = _chassis_fixture(escalation, outcomes=sink, outcome_retry=_FAST_RETRY)(env)
    client, task_queue, activities = await gen.__anext__()
    return gen, client, task_queue, activities


async def _conversation_outcome(client, task_queue):
    conversation = str(uuid.uuid4())
    await _send(client, task_queue, conversation, "hi", idle_timeout_seconds=1)
    handle = client.get_workflow_handle(f"conversation-{conversation}")
    await asyncio.wait_for(handle.result(), timeout=30)
    return conversation, handle


async def test_a_finished_conversation_is_one_outcome_row(env):  # noqa: F811
    sink = FakeOutcomes()
    gen, client, task_queue, _ = await _chassis(env, sink)
    try:
        conversation, _ = await _conversation_outcome(client, task_queue)

        (event,) = sink.events
        assert (event.role, event.task_type, event.tenant) == (
            "lana",
            "conversation",
            "acme",
        )
        assert event.conversation_id == conversation and event.workflow_run_id
        assert event.resolution == "resolved" and event.turns == 1
        assert event.started_at <= event.ended_at and event.models_used
    finally:
        await gen.aclose()


async def test_a_task_outcome_carries_the_task_name_and_resolution(env):  # noqa: F811
    sink = FakeOutcomes()
    gen, client, task_queue, _ = await _chassis(env, sink)
    try:
        handle = await client.start_workflow(
            TaskWorkflow.run,
            TaskParams(task="daily_check", tenant="acme"),
            id=f"task-{uuid.uuid4()}",
            task_queue=task_queue,
        )
        await asyncio.wait_for(_wait_for_pending(handle), timeout=20)
        await handle.signal(
            TaskWorkflow.approve, ApprovalSignal(approved=True, approver="aida")
        )
        await asyncio.wait_for(handle.result(), timeout=20)

        (event,) = sink.events
        assert event.task_type == "daily_check" and event.conversation_id == handle.id
        assert event.resolution == "escalated"  # the human wait is an escalation
        assert event.tool_calls == 1 and event.escalations == 1
    finally:
        await gen.aclose()


async def test_an_unanswered_approval_is_recorded_as_expired(env):  # noqa: F811
    sink = FakeOutcomes()
    gen, client, task_queue, _ = await _chassis(
        env, sink, {"remind_after": "PT1S", "expire_after": "PT2S"}
    )
    try:
        handle = await client.start_workflow(
            TaskWorkflow.run,
            TaskParams(task="daily_check", tenant="acme"),
            id=f"task-{uuid.uuid4()}",
            task_queue=task_queue,
        )
        await asyncio.wait_for(handle.result(), timeout=30)

        assert [e.resolution for e in sink.events] == ["expired"]
    finally:
        await gen.aclose()


async def test_a_transient_outage_is_retried_through(env):  # noqa: F811
    sink = FakeOutcomes(fail_first=2)  # down for two attempts, then back
    gen, client, task_queue, _ = await _chassis(env, sink)
    try:
        await _conversation_outcome(client, task_queue)

        assert sink.attempts == 3 and len(sink.events) == 1
    finally:
        await gen.aclose()


async def test_giving_up_never_holds_the_workflow_open(env):  # noqa: F811
    """The store never comes back: after the bounded attempts the workflow
    completes anyway. An outcome is a metric, not a reason to wedge a workflow."""
    sink = FakeOutcomes(always_fail=True)
    gen, client, task_queue, activities = await _chassis(env, sink)
    try:
        _, handle = await _conversation_outcome(client, task_queue)

        assert sink.attempts == 3 and sink.events == []  # exactly the policy's attempts
        assert (await handle.describe()).status == WorkflowExecutionStatus.COMPLETED
        # The full event still went to the log line the activity writes first
        # (and to the in-process tail), so the row can be backfilled.
        assert len(activities.emitted_outcomes) == 1
    finally:
        await gen.aclose()
