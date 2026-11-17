"""GET /v1/tasks against a real Temporal dev server: a task that nobody started
through the API shows up too, it reports whether it is waiting for a person, and
it is scoped to this Employee's task queue."""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent_chassis.channels.http import create_app
from agent_chassis.config import get_settings
from agent_chassis.durable.models import TaskParams
from agent_chassis.durable.runtime_context import get_role_runtime
from agent_chassis.durable.workflows import TaskWorkflow
from tests.integration.test_conversation_workflow import (  # noqa: F401
    chassis,
    env,
    settings,
)

AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture
async def api(chassis, monkeypatch):  # noqa: F811
    client, task_queue, _ = chassis
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", task_queue)
    monkeypatch.setenv("HTTP_BEARER_TOKENS", "tok:aida")
    get_settings.cache_clear()

    async def get_client():
        return client

    app = create_app(get_client, resolved=get_role_runtime().resolved)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, client
    get_settings.cache_clear()


async def _list_until(http, predicate, timeout=20.0):
    """Visibility is eventually consistent: poll instead of sleeping."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        rows = (await http.get("/v1/tasks", headers=AUTH)).json()
        if predicate(rows):
            return rows
        assert asyncio.get_event_loop().time() < deadline, f"never matched: {rows}"
        await asyncio.sleep(0.3)


async def test_a_waiting_task_is_listed_with_its_state_and_then_completes(api):
    http, _ = api
    started = (
        await http.post("/v1/tasks", json={"task": "daily_check"}, headers=AUTH)
    ).json()

    rows = await _list_until(
        http, lambda r: any(x["state"] == "waiting_on_human" for x in r)
    )

    (row,) = [r for r in rows if r["workflow_id"] == started["workflow_id"]]
    assert row["task"] == "daily_check" and row["status"] == "RUNNING"
    assert row["pending_approvals"] == 1 and row["closed_at"] is None

    detail = (
        await http.get(f"/v1/tasks/{started['workflow_id']}", headers=AUTH)
    ).json()
    call_id = detail["pending"][0]["call_id"]
    decided = await http.post(
        "/v1/approvals",
        json={
            "workflow_id": started["workflow_id"],
            "call_id": call_id,
            "approved": True,
        },
        headers=AUTH,
    )
    assert decided.json() == {"status": "delivered", "approver": "aida"}

    done = await _list_until(
        http,
        lambda r: any(
            x["workflow_id"] == started["workflow_id"] and x["status"] == "COMPLETED"
            for x in r
        ),
    )
    finished = next(r for r in done if r["workflow_id"] == started["workflow_id"])
    assert finished["state"] is None and finished["closed_at"] is not None


async def test_the_list_is_scoped_to_this_employees_task_queue(api):
    http, client = api
    mine = (
        await http.post("/v1/tasks", json={"task": "daily_check"}, headers=AUTH)
    ).json()
    # Another Employee's unit, sharing this Temporal namespace (no worker needed).
    other = f"task-daily_check-{uuid.uuid4()}"
    await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=other,
        task_queue="someone-elses-queue",
    )

    # Absence only means something once the foreign run is visible in Temporal at all
    # AND ours is listed: wait for both, or this passes on an indexing delay.
    for _ in range(200):
        if [w async for w in client.list_workflows(f"WorkflowId = '{other}'")]:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("the other Employee's run never became visible")
    rows = await _list_until(
        http, lambda r: any(x["workflow_id"] == mine["workflow_id"] for x in r)
    )

    assert other not in {r["workflow_id"] for r in rows}


async def test_another_employees_task_cannot_be_read_or_decided_through_this_unit(api):
    """Employees share a Temporal namespace, so a workflow id alone proves nothing. A task on
    another Employee's queue answers exactly like an unknown id, and an approval aimed at it
    must not reach it."""
    http, client = api
    foreign = f"task-daily_check-{uuid.uuid4()}"
    await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=foreign,
        task_queue="someone-elses-queue",
    )

    detail = await http.get(f"/v1/tasks/{foreign}", headers=AUTH)
    decision = await http.post(
        "/v1/approvals",
        json={"workflow_id": foreign, "call_id": "call_1", "approved": True},
        headers=AUTH,
    )
    unknown = await http.get(
        f"/v1/tasks/task-never-existed-{uuid.uuid4()}", headers=AUTH
    )

    assert detail.status_code == 404 and decision.status_code == 404
    # Indistinguishable from an id that does not exist: no hint that it is somebody's.
    assert (
        detail.json()["detail"].split("'")[0] == unknown.json()["detail"].split("'")[0]
    )
    history = await client.get_workflow_handle(foreign).fetch_history()
    assert not [e for e in history.events if "SIGNALED" in str(e.event_type)], (
        "the approval signal reached the other Employee's task"
    )


async def test_the_list_is_newest_first_even_when_an_older_run_is_still_open(api):
    """Temporal lists open runs first by default. An older run still waiting for a person
    must not outrank a newer one that has already finished."""
    http, _ = api
    older = (
        await http.post("/v1/tasks", json={"task": "daily_check"}, headers=AUTH)
    ).json()
    await _list_until(
        http,
        lambda r: any(
            x["workflow_id"] == older["workflow_id"]
            and x["state"] == "waiting_on_human"
            for x in r
        ),
    )
    newer = (
        await http.post("/v1/tasks", json={"task": "daily_check"}, headers=AUTH)
    ).json()
    await _list_until(
        http,
        lambda r: any(
            x["workflow_id"] == newer["workflow_id"]
            and x["state"] == "waiting_on_human"
            for x in r
        ),
    )
    pending = (
        await http.get(f"/v1/tasks/{newer['workflow_id']}", headers=AUTH)
    ).json()["pending"][0]
    await http.post(
        "/v1/approvals",
        json={
            "workflow_id": newer["workflow_id"],
            "call_id": pending["call_id"],
            "approved": True,
        },
        headers=AUTH,
    )
    rows = await _list_until(
        http,
        lambda r: any(
            x["workflow_id"] == newer["workflow_id"] and x["status"] == "COMPLETED"
            for x in r
        ),
    )

    ids = [r["workflow_id"] for r in rows]
    assert ids.index(newer["workflow_id"]) < ids.index(older["workflow_id"])
    stamps = [r["started_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


async def test_a_task_started_by_a_schedule_is_recognised_by_its_id(api):
    """Schedules start ``task-<employee>-<task>-<time>``; the list still names the task."""
    http, client = api
    queue = get_settings().temporal_task_queue
    scheduled = f"task-lana-daily_check-2026-11-16T08:00:00Z-{uuid.uuid4().hex[:6]}"
    await client.start_workflow(
        TaskWorkflow.run,
        TaskParams(task="daily_check", tenant="acme"),
        id=scheduled,
        task_queue=queue,
    )

    rows = await _list_until(
        http, lambda r: any(x["workflow_id"] == scheduled for x in r)
    )

    assert (
        next(r for r in rows if r["workflow_id"] == scheduled)["task"] == "daily_check"
    )


async def test_the_list_requires_authentication(api):
    http, _ = api
    assert (await http.get("/v1/tasks")).status_code == 401
