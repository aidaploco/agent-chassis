"""Temporal Schedules from the manifest (D6): each ``schedules`` entry
becomes one Schedule starting TaskWorkflow on its cron. Idempotent — the
worker ensures them on every start, existing schedules are left alone."""

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
)

from agent_chassis.durable.models import TaskParams
from agent_chassis.durable.workflows import TaskWorkflow
from agent_chassis.manifest.schema import RoleManifest
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


async def ensure_schedules(
    client: Client, manifest: RoleManifest, task_queue: str, tenant: str
) -> None:
    for entry in manifest.schedules:
        schedule_id = f"{manifest.employee.name}-{entry.name}"
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        TaskWorkflow.run,
                        TaskParams(task=entry.task, tenant=tenant),
                        # The Schedule appends the fire time, so runs get
                        # unique workflow ids off this prefix.
                        id=f"task-{manifest.employee.name}-{entry.task}",
                        task_queue=task_queue,
                    ),
                    spec=ScheduleSpec(cron_expressions=[entry.cron]),
                ),
            )
            _logger.info("schedule created id=%s cron=%s", schedule_id, entry.cron)
        except ScheduleAlreadyRunningError:
            _logger.info("schedule exists id=%s", schedule_id)
