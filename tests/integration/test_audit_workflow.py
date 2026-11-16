"""Audit entries from the real workflows (D11), against the Temporal dev server.

The sink is an in-memory fake with the same idempotency contract as Postgres.
What is under test is the workflow behaviour you chose for it: an approval is
recorded BEFORE the workflow acts on it, and a store outage stalls approvals
(fail closed) but never an escalation (best effort).
"""

import asyncio
import uuid

import pytest
from temporalio.client import WorkflowExecutionStatus

from agent_chassis.audit import AuditEvent
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


class FakeAudit:
    """In-memory audit sink: dedupes on (kind, dedupe_key) like the real one."""

    def __init__(self, *, fail_while=None) -> None:
        self.events: list[AuditEvent] = []
        self.attempts = 0
        # A callable returning True while writes should fail (a store outage).
        self._fail_while = fail_while

    async def record(self, event: AuditEvent) -> bool:
        self.attempts += 1
        if self._fail_while is not None and self._fail_while():
            raise ConnectionError("audit store unreachable")
        if any(
            (e.kind, e.dedupe_key) == (event.kind, event.dedupe_key)
            for e in self.events
        ):
            return False
        self.events.append(event)
        return True

    def of(self, kind: str) -> list[AuditEvent]:
        return [e for e in self.events if e.kind == kind]


_FAST = {"remind_after": "PT1S", "expire_after": "PT3S"}


@pytest.fixture
async def audited(env):  # noqa: F811
    sink = FakeAudit()
    gen = _chassis_fixture(audit=sink)(env)
    client, task_queue, activities = await gen.__anext__()
    try:
        yield client, task_queue, activities, sink
    finally:
        await gen.aclose()


@pytest.fixture
async def audited_fast_expiry(env):  # noqa: F811
    sink = FakeAudit()
    gen = _chassis_fixture(_FAST, audit=sink)(env)
    client, task_queue, _ = await gen.__anext__()
    try:
        yield client, task_queue, sink
    finally:
        await gen.aclose()


async def _start_task(client, task_queue, task="daily_check"):
    return await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task=task, tenant="acme"),
        id=f"task-{uuid.uuid4()}",
        task_queue=task_queue,
    )


async def test_an_approval_is_audited_with_who_and_why(audited):
    client, task_queue, _, sink = audited
    handle = await _start_task(client, task_queue)
    await asyncio.wait_for(_wait_for_pending(handle), timeout=20)

    await handle.signal(
        TaskWorkflow.approve,
        ApprovalSignal(approved=True, approver="aida", comment="ship it"),
    )
    reply = await asyncio.wait_for(handle.result(), timeout=20)
    assert reply.approvals == 1

    (approval,) = sink.of("approval")
    assert (approval.actor, approval.action) == ("aida", "approved")
    assert approval.subject == handle.id and approval.employee == "lana"
    assert approval.detail["tool"] == "publish_summary"
    assert approval.detail["comment"] == "ship it"
    # The pause itself is on the record too, as an escalation.
    (pause,) = sink.of("escalation")
    assert pause.action == "escalated" and "publish_summary" in pause.detail["reason"]


async def test_an_expiry_is_audited_as_the_system_not_a_person(audited_fast_expiry):
    client, task_queue, sink = audited_fast_expiry
    handle = await _start_task(client, task_queue)

    reply = await asyncio.wait_for(handle.result(), timeout=30)
    assert reply.expired == 1

    (decision,) = sink.of("approval")
    assert (decision.actor, decision.action) == ("system", "expired")
    # Reminders are labelled as such, distinct from the escalation they follow.
    actions = [e.action for e in sink.of("escalation")]
    assert actions[0] == "escalated" and "reminded" in actions


async def test_an_approval_is_not_acted_on_until_it_is_audited(env):  # noqa: F811
    """Fail closed: while the audit store is down the approval is retried, the
    task does not complete, and the tool does not run. When the store returns,
    exactly one row lands and the task finishes."""
    outage = {"down": True}
    sink = FakeAudit(fail_while=lambda: outage["down"])
    gen = _chassis_fixture(audit=sink)(env)
    client, task_queue, _ = await gen.__anext__()
    try:
        handle = await _start_task(client, task_queue)
        await asyncio.wait_for(_wait_for_pending(handle), timeout=20)
        # The pause's own audit write is best-effort, so it does not stall.
        await handle.signal(
            TaskWorkflow.approve, ApprovalSignal(approved=True, approver="aida")
        )

        await asyncio.sleep(4)  # several retries' worth
        assert sink.attempts >= 2  # it kept trying...
        assert sink.of("approval") == []  # ...and nothing was recorded
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING

        outage["down"] = False
        reply = await asyncio.wait_for(handle.result(), timeout=30)

        assert "published" in reply.content  # the approved tool ran, after the audit
        assert len(sink.of("approval")) == 1
    finally:
        await gen.aclose()


async def test_an_audit_outage_never_blocks_an_escalation(env):  # noqa: F811
    sink = FakeAudit(fail_while=lambda: True)  # permanently down
    gen = _chassis_fixture(audit=sink)(env)
    client, task_queue, _ = await gen.__anext__()
    try:
        reply = await asyncio.wait_for(
            _send(client, task_queue, str(uuid.uuid4()), "please escalate me"),
            timeout=20,
        )

        assert reply.escalated  # the user got their answer
        assert sink.attempts >= 1 and sink.events == []
    finally:
        await gen.aclose()


async def test_a_conversation_escalation_is_audited(audited):
    client, task_queue, _, sink = audited
    conversation = str(uuid.uuid4())

    await _send(client, task_queue, conversation, "please escalate me")

    (event,) = sink.of("escalation")
    assert event.subject == conversation and event.action == "escalated"
    assert event.detail["reason"] == "user asked"
    assert event.detail["agent"] == "supervisor"
