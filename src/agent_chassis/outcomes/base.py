from typing import Protocol

from agent_chassis.contracts import OutcomeEvent


class OutcomeSink(Protocol):
    """Where finished conversations and tasks are recorded as metrics rows."""

    async def record(self, event: OutcomeEvent) -> bool:
        """Append ``event``. Returns ``True`` if it was written, ``False`` if a
        row for the same ``(role, workflow_run_id)`` already existed (an activity
        retry or a replay).

        Raises if the store cannot be reached: the activity is retried by
        Temporal, and the workflow gives up after a bounded number of attempts."""
        ...
