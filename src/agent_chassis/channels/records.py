"""Read-only endpoints over the audit stream (D11) and the outcome feed (D12).

The worker owns both tables (it creates them and writes to them); the api only
reads. Several Employees share one Postgres and one table each, so every read is
scoped to THIS Employee by the manifest's name, never by anything the caller sends.
Both endpoints require authentication: audit rows carry approver identities,
comments and approval-gated arguments.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

MAX_ROWS = 200


class AuditReader(Protocol):
    async def read(
        self, *, employee: str, kind: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...


class OutcomeReader(Protocol):
    async def read(self, *, role: str, limit: int = 50) -> list[dict[str, Any]]: ...


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval", "manifest_override", "escalation"]
    employee: str
    tenant: str
    subject: str
    actor: str
    action: str
    detail: dict[str, Any]
    occurred_at: datetime
    recorded_at: datetime


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_version: int
    role: str
    tenant: str
    task_type: str
    conversation_id: str
    workflow_run_id: str
    resolution: str
    turns: int
    tool_calls: int
    wall_time_ms: int
    tokens_in: int
    tokens_out: int
    escalations: int
    guardrail_trips: int
    models_used: list[str]
    started_at: datetime
    ended_at: datetime
    recorded_at: datetime


def register_record_routes(
    app: FastAPI,
    *,
    employee: str,
    get_audit: Callable[[], Awaitable[AuditReader | None]] | None,
    get_outcomes: Callable[[], Awaitable[OutcomeReader | None]] | None,
    auth: Callable[..., Awaitable[str]],
) -> None:
    """Add ``GET /v1/audit`` and ``GET /v1/outcomes`` for ``employee``."""

    def unavailable(what: str, error: Exception | None = None) -> HTTPException:
        if error is not None:
            _logger.warning("%s read failed: %s", what, error)
        return HTTPException(
            status_code=503,
            detail=f"the {what} store is unavailable: POSTGRES_URL is unset, the"
            " database is unreachable, or the worker has not created the table yet",
        )

    @app.get("/v1/audit", response_model=list[AuditRecord])
    async def list_audit(
        principal: Annotated[str, Depends(auth)],
        kind: Literal["approval", "manifest_override", "escalation"] | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 50,
    ) -> list[AuditRecord]:
        """Newest first: approvals, escalations and manifest overrides for this
        Employee."""
        store = await get_audit() if get_audit else None
        if store is None:
            raise unavailable("audit")
        try:
            rows = await store.read(employee=employee, kind=kind, limit=limit)
        except psycopg.Error as exc:
            raise unavailable("audit", exc) from exc
        return [AuditRecord.model_validate(row) for row in rows]

    @app.get("/v1/outcomes", response_model=list[OutcomeRecord])
    async def list_outcomes(
        principal: Annotated[str, Depends(auth)],
        limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 50,
    ) -> list[OutcomeRecord]:
        """Newest first: one row per finished conversation or task of this
        Employee."""
        store = await get_outcomes() if get_outcomes else None
        if store is None:
            raise unavailable("outcome")
        try:
            rows = await store.read(role=employee, limit=limit)
        except psycopg.Error as exc:
            raise unavailable("outcome", exc) from exc
        return [OutcomeRecord.model_validate(row) for row in rows]
