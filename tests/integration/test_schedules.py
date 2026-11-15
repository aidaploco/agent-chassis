"""Manifest schedules against a real Temporal dev server: the worker syncs
Temporal to the manifest — create, update, and delete."""

import asyncio

import yaml
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
)
from temporalio.service import RPCError

from agent_chassis.durable.models import TaskParams
from agent_chassis.durable.schedules import ensure_schedules
from agent_chassis.durable.workflows import TaskWorkflow
from agent_chassis.manifest import RoleManifest

# Shared fixtures: the same dev server (payload codec on).
from tests.integration.test_conversation_workflow import (  # noqa: F401
    FIXTURES,
    env,
    settings,
)


def _manifest(schedules: list[dict]) -> RoleManifest:
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["tasks"] = [{"name": "daily_check", "agent": "billing", "prompt": "Check."}]
    data["kpis"]["task_types"] = ["conversation", "daily_check"]
    data["schedules"] = schedules
    return RoleManifest.model_validate(data)


def _daily(hour: int) -> dict:
    return {"name": "daily_morning", "cron": f"0 {hour} * * *", "task": "daily_check"}


async def _fire_hour(client, schedule_id: str) -> int:
    """The hour the schedule fires at. The server may hand a cron back as a
    string or as a structured calendar; read whichever it gives."""
    spec = (await client.get_schedule_handle(schedule_id).describe()).schedule.spec
    if spec.cron_expressions:
        return int(spec.cron_expressions[0].split()[1])
    return spec.calendars[0].hour[0].start


async def _exists(client, schedule_id: str) -> bool:
    try:
        await client.get_schedule_handle(schedule_id).describe()
        return True
    except RPCError:
        return False


async def test_schedules_follow_the_manifest(env):  # noqa: F811
    client = env.client
    # A schedule that belongs to someone else must never be touched.
    await client.create_schedule(
        "lana_eu-daily_morning",
        Schedule(
            action=ScheduleActionStartWorkflow(
                TaskWorkflow.run,
                TaskParams(task="daily_check", tenant="acme"),
                id="task-lana_eu-daily_check",
                task_queue="elsewhere",
            ),
            spec=ScheduleSpec(cron_expressions=["0 6 * * *"]),
        ),
    )

    await ensure_schedules(client, _manifest([_daily(8)]), "tq", "acme")
    assert await _fire_hour(client, "lana-daily_morning") == 8

    # Idempotent, and an edited cron takes effect (it used to be ignored).
    await ensure_schedules(client, _manifest([_daily(8)]), "tq", "acme")
    await ensure_schedules(client, _manifest([_daily(9)]), "tq", "acme")
    assert await _fire_hour(client, "lana-daily_morning") == 9

    # Dropped from the manifest → deleted (it used to fire forever). Listing
    # is eventually consistent, so sync until it is gone.
    async def removed() -> None:
        while await _exists(client, "lana-daily_morning"):
            await ensure_schedules(client, _manifest([]), "tq", "acme")
            await asyncio.sleep(0.2)

    await asyncio.wait_for(removed(), timeout=20)
    assert await _fire_hour(client, "lana_eu-daily_morning") == 6
