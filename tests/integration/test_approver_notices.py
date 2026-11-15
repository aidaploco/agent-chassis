"""Escalation and approval notices reach the approvers' channel (D10).

Real Temporal dev server and the real workflows; the approvers' webhook is a
fake. What is under test: one notice per escalation / pause, reminders with
distinct keys, a dead webhook not blocking the conversation, and — the
security-relevant one — that a notice (internal reasons, tool arguments) can
never be delivered through the customer-facing sender.
"""

import asyncio
import uuid

import pytest
from temporalio.client import WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy

from agent_chassis.durable.models import ConversationParams, TaskParams, TurnReply
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow

# Shared fixtures/fakes: same dev server, packs and scripted model.
from tests.integration.test_conversation_workflow import (  # noqa: F401
    FAKE_DELIVERIES,
    _chassis_fixture,
    _envelope,
    env,
    settings,
)

APPROVER_NOTICES: list = []


class RecordingApprovers:
    """The approvers' webhook: records deliveries, dedupes by key like a real
    sender must (D15)."""

    async def deliver(self, delivery) -> None:
        if any(d.key == delivery.key for d in APPROVER_NOTICES):
            return
        APPROVER_NOTICES.append(delivery)


class DeadApprovers:
    """An approvers' webhook that is down."""

    attempts = 0

    async def deliver(self, delivery) -> None:
        type(self).attempts += 1
        raise RuntimeError("approvers webhook unreachable")


_SLACK_APPROVERS = {
    "approvers_channel": "slack",
    "remind_after": "PT1S",
    "expire_after": "PT4S",
}


@pytest.fixture
async def notified(env):  # noqa: F811
    APPROVER_NOTICES.clear()
    gen = _chassis_fixture(_SLACK_APPROVERS, approvers_sender=RecordingApprovers())(env)
    chassis = await gen.__anext__()
    try:
        yield chassis
    finally:
        await gen.aclose()


@pytest.fixture
async def dead_approvers(env):  # noqa: F811
    DeadApprovers.attempts = 0
    gen = _chassis_fixture(_SLACK_APPROVERS, approvers_sender=DeadApprovers())(env)
    chassis = await gen.__anext__()
    try:
        yield chassis
    finally:
        await gen.aclose()


async def _send_on_slack(client, task_queue, conversation: str, text: str) -> TurnReply:
    start_op = WithStartWorkflowOperation(
        ConversationWorkflow.run,
        ConversationParams(
            conversation_id=conversation, tenant="acme", channel="slack"
        ),
        id=f"conversation-{conversation}",
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    envelope = _envelope(conversation, text).model_copy(update={"channel": "slack"})
    return await client.execute_update_with_start_workflow(
        ConversationWorkflow.send_message,
        envelope,
        start_workflow_operation=start_op,
    )


async def test_conversation_escalation_notifies_approvers_not_the_customer(notified):
    client, task_queue, _ = notified
    conversation = f"slack-C42-{uuid.uuid4().hex[:6]}"

    reply = await _send_on_slack(client, task_queue, conversation, "please escalate me")
    assert reply.escalated

    # Exactly one notice, actionable: why, who, where, which conversation.
    assert len(APPROVER_NOTICES) == 1
    notice = APPROVER_NOTICES[0]
    assert notice.key == f"{conversation}:escalation:1"
    assert notice.channel == "slack"
    assert notice.metadata == {"audience": "approvers", "kind": "escalation"}
    assert "user asked" in notice.text  # the reason
    assert "With: u-1 (via slack)" in notice.text
    assert f"Conversation: {conversation}" in notice.text

    # It is not addressed to the customer's conversation, and nothing internal
    # went through the customer-facing sender: that only carried the ack. The
    # ack is pushed after the update returns, so wait for it rather than race.
    async def ack_delivered() -> None:
        while not FAKE_DELIVERIES:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(ack_delivered(), timeout=15)
    assert not notice.conversation_id.startswith("slack-")
    customer_facing = [d.text for d in FAKE_DELIVERIES]
    assert customer_facing and all("user asked" not in t for t in customer_facing)
    assert all(d.key != notice.key for d in FAKE_DELIVERIES)


async def test_task_pause_and_reminders_notify_approvers(notified):
    client, task_queue, activities = notified
    handle = await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=f"task-{uuid.uuid4()}",
        task_queue=task_queue,
    )
    reply = await asyncio.wait_for(handle.result(), timeout=30)  # nobody approves
    assert reply.expired == 1

    workflow_id = handle.id
    by_kind = {}
    for delivery in APPROVER_NOTICES:
        by_kind.setdefault(delivery.metadata["kind"], []).append(delivery)

    (pause,) = by_kind["approval"]
    assert pause.key == f"{workflow_id}:approval:1"  # after the first task turn
    assert "publish_summary" in pause.text and "all green" in pause.text
    assert workflow_id in pause.text and "/v1/approvals" in pause.text

    assert by_kind["reminder"], (
        "remind_after=PT1S, expire_after=PT4S: expected a reminder"
    )
    keys = [d.key for d in by_kind["reminder"]]
    assert len(set(keys)) == len(keys)  # distinct, so none is deduped away
    assert keys[0] == f"{workflow_id}:reminder:1:1"  # pause 1, first reminder
    assert "still waiting" in by_kind["reminder"][0].text


async def test_dead_approvers_webhook_does_not_block_the_conversation(dead_approvers):
    client, task_queue, _ = dead_approvers
    conversation = f"conv-{uuid.uuid4().hex[:6]}"

    first = await asyncio.wait_for(
        _send_on_slack(client, task_queue, conversation, "please escalate me"),
        timeout=40,
    )
    assert first.escalated  # the customer still got their answer
    second = await asyncio.wait_for(
        _send_on_slack(client, task_queue, conversation, "hello again"), timeout=40
    )

    # The next turn ran (the scripted model's wording after an escalation is not
    # what is being tested): the dead webhook cost the notice, not the turn.
    assert second.turn_number == 2 and not second.escalated
    assert DeadApprovers.attempts >= 4  # retried, then gave up
