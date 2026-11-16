import json
import uuid
from datetime import UTC
from typing import LiteralString

from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from agent_chassis.audit.base import AuditEvent
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

# Serialises start() across worker replicas so concurrent DDL cannot race.
_DDL_LOCK_KEY = 7_203_411_901


class PgAuditSink:
    """The audit stream in Postgres: one append-only table (D11).

    Append-only is enforced by the database, not by convention: triggers reject
    ``UPDATE``, ``DELETE`` and ``TRUNCATE`` for every role that does not first
    drop the trigger, so a bug (or an operator's slip) in the application cannot
    rewrite history. A database superuser can still remove the triggers; this
    makes tampering deliberate and visible, not impossible.
    """

    def __init__(self, dsn: str, table: str = "chassis_audit") -> None:
        self._table = table
        # Composed, never interpolated: names are quoted as identifiers.
        self._t = sql.Identifier(table)
        self._pool = AsyncConnectionPool(dsn, open=False, kwargs={"autocommit": True})

    async def start(self) -> None:
        await self._pool.open()
        function = sql.Identifier(f"{self._table}_append_only")
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                sql.SQL("SELECT pg_advisory_xact_lock({})").format(
                    sql.Literal(_DDL_LOCK_KEY)
                )
            )
            await conn.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        id UUID PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        occurred_at TIMESTAMPTZ NOT NULL,
                        employee TEXT NOT NULL,
                        tenant TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        detail JSONB NOT NULL DEFAULT '{{}}',
                        dedupe_key TEXT NOT NULL
                    )
                    """
                ).format(table=self._t)
            )
            await conn.execute(
                sql.SQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS {index}"
                    " ON {table} (kind, dedupe_key)"
                ).format(
                    index=sql.Identifier(f"{self._table}_dedupe_idx"), table=self._t
                )
            )
            await conn.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index}"
                    " ON {table} (employee, recorded_at)"
                ).format(
                    index=sql.Identifier(f"{self._table}_employee_idx"), table=self._t
                )
            )
            await conn.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index} ON {table} (subject)"
                ).format(
                    index=sql.Identifier(f"{self._table}_subject_idx"), table=self._t
                )
            )
            await conn.execute(
                sql.SQL(
                    """
                    CREATE OR REPLACE FUNCTION {function}() RETURNS trigger
                    LANGUAGE plpgsql AS $$
                    BEGIN
                        RAISE EXCEPTION 'the audit table is append-only: % is not allowed',
                            TG_OP;
                    END
                    $$
                    """
                ).format(function=function)
            )
            triggers: tuple[tuple[LiteralString, LiteralString, LiteralString], ...] = (
                ("rows", "UPDATE OR DELETE", "ROW"),
                ("truncate", "TRUNCATE", "STATEMENT"),
            )
            for suffix, event, scope in triggers:
                trigger = sql.Identifier(f"{self._table}_no_{suffix}")
                await conn.execute(
                    sql.SQL("DROP TRIGGER IF EXISTS {trigger} ON {table}").format(
                        trigger=trigger, table=self._t
                    )
                )
                await conn.execute(
                    sql.SQL(
                        "CREATE TRIGGER {trigger} BEFORE {event} ON {table}"
                        " FOR EACH {scope} EXECUTE FUNCTION {function}()"
                    ).format(
                        trigger=trigger,
                        event=sql.SQL(event),
                        table=self._t,
                        scope=sql.SQL(scope),
                        function=function,
                    )
                )
        _logger.info("audit store ready table=%s", self._table)

    async def close(self) -> None:
        await self._pool.close()

    async def record(self, event: AuditEvent) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {table}
                        (id, occurred_at, employee, tenant, kind, subject, actor,
                         action, detail, dedupe_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kind, dedupe_key) DO NOTHING
                    RETURNING id
                    """
                ).format(table=self._t),
                (
                    str(uuid.uuid4()),
                    event.occurred_at.astimezone(UTC),
                    event.employee,
                    event.tenant,
                    event.kind,
                    event.subject,
                    event.actor,
                    event.action,
                    json.dumps(event.detail, default=str),
                    event.dedupe_key,
                ),
            )
            return await cursor.fetchone() is not None

    async def recent(self, *, kind: str | None = None, limit: int = 20) -> list[dict]:
        """Newest rows first, for tests and debugging (there is no read API)."""
        clause = sql.SQL("WHERE kind = %s" if kind else "")
        params: list = [kind] if kind else []
        params.append(limit)
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                sql.SQL(
                    """
                    SELECT kind, employee, tenant, subject, actor, action, detail,
                           dedupe_key, occurred_at, recorded_at
                    FROM {table} {clause}
                    ORDER BY recorded_at DESC, id LIMIT %s
                    """
                ).format(table=self._t, clause=clause),
                params,
            )
            columns = [c.name for c in cursor.description or []]
            return [
                dict(zip(columns, row, strict=True)) for row in await cursor.fetchall()
            ]
