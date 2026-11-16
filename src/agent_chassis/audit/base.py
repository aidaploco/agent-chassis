from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    """One entry in the audit stream (D11).

    ``actor`` is who caused it: the authenticated principal for an approval
    decision (D21), ``system`` for an expiry, the employee's name for an
    escalation, ``manifest`` for an override. ``dedupe_key`` makes a write
    idempotent per ``kind``: activities are retried and workers restart, and
    neither may add a second row for the same fact.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval", "manifest_override", "escalation"]
    employee: str
    tenant: str
    # What it is about: a workflow id, a conversation id, or "pack.setting".
    subject: str
    actor: str
    action: Literal[
        "approved", "denied", "expired", "override", "escalated", "reminded"
    ]
    detail: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str
    # When the fact happened (a decision's decided_at), as opposed to when it was
    # recorded: the store stamps its own recorded_at.
    occurred_at: datetime


class AuditSink(Protocol):
    """Where audit events go."""

    async def record(self, event: AuditEvent) -> bool:
        """Append ``event``. Returns ``True`` if it was written, ``False`` if an
        event with the same ``(kind, dedupe_key)`` already existed.

        Raises if the store cannot be reached: callers decide whether that
        blocks them (approvals do) or is only logged (escalations)."""
        ...
