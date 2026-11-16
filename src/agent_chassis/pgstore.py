"""Postgres plumbing shared by the stores that own a table of their own: the
audit stream (D11) and the outcome feed (D12).

Each store keeps its own schema and queries; what they share is how a pool is
built, how DDL is serialised across worker replicas, and how a table is made
append-only.
"""

from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool


def make_pool(dsn: str) -> AsyncConnectionPool:
    """A small, self-healing pool for one store.

    ``min_size=1`` / ``max_size=4``: these stores write a row now and then, and a
    worker already holds the memory substrate's pool, so each store must not sit
    on its own set of idle connections. ``check_connection`` validates a
    connection as it is handed out, so after a Postgres restart the next write
    gets a fresh one instead of failing on a dead socket.
    """
    return AsyncConnectionPool(
        dsn,
        open=False,
        min_size=1,
        max_size=4,
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": True},
    )


async def ddl_lock(conn: AsyncConnection, name: str) -> None:
    """Take a transaction-scoped advisory lock named ``name``.

    Call it first inside ``conn.transaction()`` so replicas that start at the
    same moment run their ``CREATE ... IF NOT EXISTS`` one after another instead
    of racing each other into a duplicate-object error.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (name,))


async def install_append_only(conn: AsyncConnection, table: str) -> None:
    """Make ``table`` reject ``UPDATE``, ``DELETE`` and ``TRUNCATE``.

    Idempotent: safe on every start. A database superuser can still drop the
    triggers; this makes rewriting history deliberate and visible, not impossible.
    """
    target = sql.Identifier(table)
    function = sql.Identifier(f"{table}_append_only")
    await conn.execute(
        sql.SQL(
            """
            CREATE OR REPLACE FUNCTION {function}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'the table is append-only: % is not allowed',
                    TG_OP;
            END
            $$
            """
        ).format(function=function)
    )
    for suffix, event, scope in (
        ("rows", sql.SQL("UPDATE OR DELETE"), sql.SQL("ROW")),
        ("truncate", sql.SQL("TRUNCATE"), sql.SQL("STATEMENT")),
    ):
        trigger = sql.Identifier(f"{table}_no_{suffix}")
        await conn.execute(
            sql.SQL("DROP TRIGGER IF EXISTS {trigger} ON {table}").format(
                trigger=trigger, table=target
            )
        )
        await conn.execute(
            sql.SQL(
                "CREATE TRIGGER {trigger} BEFORE {event} ON {table}"
                " FOR EACH {scope} EXECUTE FUNCTION {function}()"
            ).format(
                trigger=trigger,
                event=event,
                table=target,
                scope=scope,
                function=function,
            )
        )
