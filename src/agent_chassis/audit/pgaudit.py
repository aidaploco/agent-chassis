import json
import uuid
from datetime import UTC

from psycopg import sql

from agent_chassis.audit.base import AuditEvent
from agent_chassis.pgstore import ddl_lock, install_append_only, make_pool
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

# A read that cannot get a connection fails in seconds (the api reports the store as
# unavailable) instead of waiting out the pool's default of 30.
_READ_TIMEOUT = 5.0


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
        self._pool = make_pool(dsn)

    async def start(self) -> None:
        await self._pool.open()
        async with self._pool.connection() as conn, conn.transaction():
            await ddl_lock(conn, f"audit:{self._table}")
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
            await install_append_only(conn, self._table)
        _logger.info("audit store ready table=%s", self._table)

    async def open_for_reads(self) -> None:
        """Open the pool without touching the schema. The worker owns the table (its
        ``start()`` creates it and installs the append-only triggers); a process that
        only reads, like the HTTP api, must not run DDL. A missing table then shows
        up as an error on the first read, which the caller reports as "unavailable"."""
        await self._pool.open()

    async def read(
        self, *, employee: str, kind: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Newest rows first for ONE Employee. Several Employees share this table,
        so a read that is not scoped by ``employee`` would hand one Employee's audit
        trail to another. ``employee`` is required for that reason."""
        clauses = [sql.SQL("employee = %s")]
        params: list = [employee]
        if kind:
            clauses.append(sql.SQL("kind = %s"))
            params.append(kind)
        params.append(limit)
        async with self._pool.connection(timeout=_READ_TIMEOUT) as conn:
            cursor = await conn.execute(
                sql.SQL(
                    """
                    SELECT kind, employee, tenant, subject, actor, action, detail,
                           occurred_at, recorded_at
                    FROM {table} WHERE {where}
                    ORDER BY recorded_at DESC, id DESC LIMIT %s
                    """
                ).format(table=self._t, where=sql.SQL(" AND ").join(clauses)),
                params,
            )
            columns = [c.name for c in cursor.description or []]
            return [
                dict(zip(columns, row, strict=True)) for row in await cursor.fetchall()
            ]

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
        """Newest rows first across ALL Employees, for tests and debugging. The API uses
        ``read``, which is scoped to one."""
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
