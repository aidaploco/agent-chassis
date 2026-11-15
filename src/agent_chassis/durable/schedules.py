"""Temporal Schedules from the manifest (D6): each ``schedules`` entry
becomes one Schedule starting TaskWorkflow on its cron.

The manifest is the source of truth, and the worker syncs to it on every
start: missing schedules are created, existing ones are updated to the
manifest's cron and task, and schedules this employee owns that the manifest
no longer declares are deleted. (They used to be create-only: editing a cron
changed nothing, and a removed schedule kept firing forever.)
"""

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from agent_chassis.durable.models import TaskParams
from agent_chassis.durable.workflows import TaskWorkflow
from agent_chassis.manifest.schema import RoleManifest
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


def schedule_id_prefix(employee: str) -> str:
    """Every schedule this employee owns has an id starting with this.
    Employee names cannot contain ``-`` (manifest ``Identifier``), so one
    employee's prefix never matches another's ids."""
    return f"{employee}-"


async def ensure_schedules(
    client: Client, manifest: RoleManifest, task_queue: str, tenant: str
) -> None:
    prefix = schedule_id_prefix(manifest.employee.name)
    declared: set[str] = set()

    for entry in manifest.schedules:
        schedule_id = f"{prefix}{entry.name}"
        declared.add(schedule_id)
        action = ScheduleActionStartWorkflow(
            TaskWorkflow.run,
            TaskParams(task=entry.task, tenant=tenant),
            # The Schedule appends the fire time, so runs get unique
            # workflow ids off this prefix.
            id=f"task-{manifest.employee.name}-{entry.task}",
            task_queue=task_queue,
        )
        spec = ScheduleSpec(cron_expressions=[entry.cron])
        try:
            await client.create_schedule(
                schedule_id, Schedule(action=action, spec=spec)
            )
            _logger.info("schedule created id=%s cron=%s", schedule_id, entry.cron)
        except ScheduleAlreadyRunningError:

            def sync(
                current: ScheduleUpdateInput,
                action: ScheduleActionStartWorkflow = action,
                spec: ScheduleSpec = spec,
            ) -> ScheduleUpdate:
                # Replace what the manifest owns (what runs, and when); keep
                # operator state such as a pause and the overlap policy.
                schedule = current.description.schedule
                schedule.action = action
                schedule.spec = spec
                return ScheduleUpdate(schedule=schedule)

            await client.get_schedule_handle(schedule_id).update(sync)
            _logger.info("schedule synced id=%s cron=%s", schedule_id, entry.cron)

    async for listing in await client.list_schedules():
        if listing.id.startswith(prefix) and listing.id not in declared:
            await client.get_schedule_handle(listing.id).delete()
            _logger.warning(
                "schedule deleted id=%s — no longer declared in the manifest",
                listing.id,
            )
