from psycopg import sql

from agent_chassis.contracts import OutcomeEvent, Resolution
from agent_chassis.pgstore import ddl_lock, install_append_only, make_pool
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

# A read that cannot get a connection fails in seconds (the api reports the store as
# unavailable) instead of waiting out the pool's default of 30.
_READ_TIMEOUT = 5.0

_COLUMNS = (
    "event_version, role, tenant, task_type, conversation_id, workflow_run_id,"
    " resolution, turns, tool_calls, wall_time_ms, tokens_in, tokens_out,"
    " escalations, guardrail_trips, models_used, started_at, ended_at"
)


class PgOutcomeSink:
    """The outcome feed in Postgres: one append-only table with a typed column
    per ``OutcomeEvent`` field (D12), so metrics are plain SQL
    (``sum(tokens_in)``, ``count(*) ... group by resolution``).

    Append-only for the same reason as the audit table: these rows feed usage,
    cost and reliability numbers, and quietly editing history would corrupt
    them. A database superuser can still drop the triggers.
    """

    def __init__(self, dsn: str, table: str = "chassis_outcomes") -> None:
        self._table = table
        # Composed, never interpolated: names are quoted as identifiers.
        self._t = sql.Identifier(table)
        self._pool = make_pool(dsn)

    async def start(self) -> None:
        await self._pool.open()
        resolutions = sql.SQL(", ").join(sql.Literal(r.value) for r in Resolution)
        async with self._pool.connection() as conn, conn.transaction():
            await ddl_lock(conn, f"outcomes:{self._table}")
            await conn.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        event_version INT NOT NULL,
                        role TEXT NOT NULL,
                        tenant TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        workflow_run_id TEXT NOT NULL,
                        resolution TEXT NOT NULL CHECK (resolution IN ({resolutions})),
                        turns INT NOT NULL CHECK (turns >= 0),
                        tool_calls INT NOT NULL CHECK (tool_calls >= 0),
                        wall_time_ms BIGINT NOT NULL CHECK (wall_time_ms >= 0),
                        tokens_in BIGINT NOT NULL CHECK (tokens_in >= 0),
                        tokens_out BIGINT NOT NULL CHECK (tokens_out >= 0),
                        escalations INT NOT NULL CHECK (escalations >= 0),
                        guardrail_trips INT NOT NULL CHECK (guardrail_trips >= 0),
                        models_used TEXT[] NOT NULL DEFAULT '{{}}',
                        started_at TIMESTAMPTZ NOT NULL,
                        ended_at TIMESTAMPTZ NOT NULL
                    )
                    """
                ).format(table=self._t, resolutions=resolutions)
            )
            await conn.execute(
                sql.SQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS {index}"
                    " ON {table} (role, workflow_run_id)"
                ).format(index=sql.Identifier(f"{self._table}_run_idx"), table=self._t)
            )
            for suffix, column in (("role", "role"), ("type", "task_type")):
                await conn.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index}"
                        " ON {table} ({column}, ended_at)"
                    ).format(
                        index=sql.Identifier(f"{self._table}_{suffix}_idx"),
                        table=self._t,
                        column=sql.Identifier(column),
                    )
                )
            await install_append_only(conn, self._table)
        _logger.info("outcome store ready table=%s", self._table)

    async def open_for_reads(self) -> None:
        """Open the pool without touching the schema (see ``PgAuditSink``): the
        worker owns the table, a read-only process must not run DDL."""
        await self._pool.open()

    async def read(self, *, role: str, limit: int = 50) -> list[dict]:
        """Newest rows first for ONE role. Several Employees share this table, so
        ``role`` is required: an unscoped read would show one Employee's usage to
        another."""
        async with self._pool.connection(timeout=_READ_TIMEOUT) as conn:
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT {columns}, recorded_at FROM {table} WHERE role = %s"
                    " ORDER BY id DESC LIMIT %s"
                ).format(table=self._t, columns=sql.SQL(_COLUMNS)),
                (role, limit),
            )
            names = [c.name for c in cursor.description or []]
            return [
                dict(zip(names, row, strict=True)) for row in await cursor.fetchall()
            ]

    async def close(self) -> None:
        await self._pool.close()

    async def record(self, event: OutcomeEvent) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                sql.SQL(
                    """
                    INSERT INTO {table} ({columns})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s)
                    ON CONFLICT (role, workflow_run_id) DO NOTHING
                    RETURNING id
                    """
                ).format(table=self._t, columns=sql.SQL(_COLUMNS)),
                (
                    event.event_version,
                    event.role,
                    event.tenant,
                    event.task_type,
                    event.conversation_id,
                    event.workflow_run_id,
                    event.resolution.value,
                    event.turns,
                    event.tool_calls,
                    event.wall_time_ms,
                    event.tokens_in,
                    event.tokens_out,
                    event.escalations,
                    event.guardrail_trips,
                    event.models_used,
                    event.started_at,
                    event.ended_at,
                ),
            )
            return await cursor.fetchone() is not None

    async def recent(self, *, limit: int = 20) -> list[dict]:
        """Newest rows first across ALL roles, for tests and debugging. The API uses
        ``read``, which is scoped to one."""
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT {columns}, recorded_at FROM {table}"
                    " ORDER BY id DESC LIMIT %s"
                ).format(table=self._t, columns=sql.SQL(_COLUMNS)),
                (limit,),
            )
            names = [c.name for c in cursor.description or []]
            return [
                dict(zip(names, row, strict=True)) for row in await cursor.fetchall()
            ]
