"""The outcome store against a real Postgres (D12): typed columns, idempotent
by run, append-only, and constrained so a bad row cannot get in."""

import asyncio
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from agent_chassis.contracts import OutcomeEvent, Resolution
from agent_chassis.outcomes import PgOutcomeSink

STARTED = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
ENDED = datetime(2026, 1, 2, 9, 5, tzinfo=UTC)


def _event(**fields) -> OutcomeEvent:
    base = {
        "role": "lana",
        "tenant": "acme",
        "task_type": "conversation",
        "conversation_id": "c-1",
        "workflow_run_id": uuid.uuid4().hex,
        "resolution": Resolution.RESOLVED,
        "turns": 3,
        "tool_calls": 2,
        "wall_time_ms": 300_000,
        "tokens_in": 1200,
        "tokens_out": 340,
        "escalations": 0,
        "guardrail_trips": 1,
        "models_used": ["gpt-5.4-mini", "gpt-4o-mini"],
        "started_at": STARTED,
        "ended_at": ENDED,
    }
    return OutcomeEvent(**{**base, **fields})


@pytest.fixture
async def sink(postgres_url):
    store = PgOutcomeSink(postgres_url, table=f"outcomes_{uuid.uuid4().hex[:8]}")
    await store.start()
    yield store
    await store.close()


async def test_every_field_round_trips_as_a_typed_column(sink):
    event = _event(workflow_run_id="run-1")

    assert await sink.record(event) is True

    (row,) = await sink.recent()
    assert row["event_version"] == 1
    assert (row["role"], row["tenant"], row["task_type"]) == (
        "lana",
        "acme",
        "conversation",
    )
    assert (row["conversation_id"], row["workflow_run_id"]) == ("c-1", "run-1")
    assert row["resolution"] == "resolved"
    assert (
        row["turns"],
        row["tool_calls"],
        row["escalations"],
        row["guardrail_trips"],
    ) == (3, 2, 0, 1)
    assert (row["tokens_in"], row["tokens_out"], row["wall_time_ms"]) == (
        1200,
        340,
        300_000,
    )
    assert row["models_used"] == ["gpt-5.4-mini", "gpt-4o-mini"]  # a real array
    assert (row["started_at"], row["ended_at"]) == (STARTED, ENDED)
    assert row["recorded_at"] > ENDED  # the store's own clock


async def test_a_retry_or_replay_is_a_no_op(sink):
    event = _event(workflow_run_id="run-1")

    assert await sink.record(event) is True
    assert await sink.record(event) is False  # the activity ran again
    assert len(await sink.recent()) == 1


async def test_concurrent_duplicates_write_exactly_one_row(sink):
    event = _event(workflow_run_id="run-1")

    results = await asyncio.gather(*(sink.record(event) for _ in range(10)))

    assert results.count(True) == 1 and len(await sink.recent()) == 1


async def test_the_same_run_id_under_another_role_is_a_different_outcome(sink):
    await sink.record(_event(role="lana", workflow_run_id="run-1"))
    await sink.record(_event(role="nova", workflow_run_id="run-1"))

    assert {r["role"] for r in await sink.recent()} == {"lana", "nova"}


async def test_every_resolution_is_storable_and_metrics_are_plain_sql(
    sink, postgres_url
):
    for resolution in Resolution:
        await sink.record(_event(resolution=resolution, tokens_in=100))
    await sink.record(_event(resolution=Resolution.RESOLVED, tokens_in=50))

    with psycopg.connect(postgres_url, autocommit=True) as conn:
        counts = dict(
            conn.execute(  # pyright: ignore[reportArgumentType]
                f"SELECT resolution, count(*) FROM {sink._table} GROUP BY 1"
            ).fetchall()
        )
        (total_in,) = conn.execute(  # pyright: ignore[reportArgumentType]
            f"SELECT sum(tokens_in) FROM {sink._table}"
        ).fetchone()
    assert counts == {"resolved": 2, "escalated": 1, "abandoned": 1, "expired": 1}
    assert total_in == 450


async def test_the_table_is_append_only_at_the_database(sink, postgres_url):
    await sink.record(_event(workflow_run_id="run-1"))
    table = sink._table

    with psycopg.connect(postgres_url, autocommit=True) as conn:
        for statement in (
            f"UPDATE {table} SET tokens_in = 0",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(statement)  # pyright: ignore[reportArgumentType]

    (row,) = await sink.recent()
    assert row["tokens_in"] == 1200


async def test_the_database_rejects_rows_the_contract_would_not(sink, postgres_url):
    """A bad value cannot get in even by a path that skips the pydantic model."""
    table = sink._table
    columns = (
        "event_version, role, tenant, task_type, conversation_id, workflow_run_id,"
        " resolution, turns, tool_calls, wall_time_ms, tokens_in, tokens_out,"
        " escalations, guardrail_trips, started_at, ended_at"
    )

    def insert(resolution: str, turns: int):
        return (
            f"INSERT INTO {table} ({columns}) VALUES (1,'r','t','c','c','x{uuid.uuid4().hex}',"
            f"'{resolution}',{turns},0,0,0,0,0,0,now(),now())"
        )

    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(insert("resolved", 1))  # pyright: ignore[reportArgumentType]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert("shrugged", 1))  # pyright: ignore[reportArgumentType]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert("resolved", -1))  # pyright: ignore[reportArgumentType]


async def test_start_is_idempotent_and_safe_to_run_concurrently(postgres_url):
    table = f"outcomes_{uuid.uuid4().hex[:8]}"
    stores = [PgOutcomeSink(postgres_url, table=table) for _ in range(4)]
    try:
        await asyncio.gather(*(s.start() for s in stores))
        await stores[0].start()
        await stores[0].record(_event())
        assert len(await stores[1].recent()) == 1
    finally:
        await asyncio.gather(*(s.close() for s in stores))


async def test_a_hostile_table_name_is_quoted_not_executed(postgres_url):
    sentinel = f"sentinel_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(f"CREATE TABLE {sentinel} (id int)")  # pyright: ignore[reportArgumentType]
        store = PgOutcomeSink(postgres_url, table=f'evil"; DROP TABLE {sentinel}; --')
        await store.start()
        try:
            await store.record(_event())
            assert len(await store.recent()) == 1
        finally:
            await store.close()
        assert conn.execute(f"SELECT count(*) FROM {sentinel}").fetchone() == (0,)  # pyright: ignore[reportArgumentType]


async def test_a_postgres_restart_does_not_strand_the_pool(postgres_url, sink):
    """check_connection hands out a live connection: a write after the server
    dropped every connection succeeds instead of failing on a dead socket."""
    await sink.record(_event(workflow_run_id="before"))
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute(  # pyright: ignore[reportArgumentType]
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE pid <> pg_backend_pid() AND datname = current_database()"
        )

    assert await sink.record(_event(workflow_run_id="after")) is True
    assert len(await sink.recent()) == 2


async def test_rows_from_the_real_store_fit_the_api_response_model(sink):
    """The API's OutcomeRecord forbids unknown fields, so a column the store returns
    that the model does not list is a 500. Validate the real shape, not a fake's."""
    from agent_chassis.channels.records import OutcomeRecord

    await sink.record(_event(role="lana"))

    (row,) = await sink.read(role="lana")

    record = OutcomeRecord.model_validate(row)
    assert record.event_version == 1 and record.role == "lana"


async def test_read_is_scoped_to_one_role(sink):
    """Lana and Nova share one table; one must never read the other's usage."""
    await sink.record(_event(role="lana"))
    await sink.record(_event(role="nova", task_type="daily_ops_report"))
    await sink.record(_event(role="lana"))

    lana = await sink.read(role="lana")

    assert [r["role"] for r in lana] == ["lana", "lana"]
    assert [r["task_type"] for r in await sink.read(role="nova")] == [
        "daily_ops_report"
    ]
    assert await sink.read(role="nobody") == []
    assert len(await sink.read(role="lana", limit=1)) == 1


async def test_a_reader_never_creates_the_table(postgres_url):
    table = f"outcomes_{uuid.uuid4().hex[:8]}"
    reader = PgOutcomeSink(postgres_url, table=table)
    await reader.open_for_reads()
    try:
        with pytest.raises(psycopg.errors.UndefinedTable):
            await reader.read(role="lana")
    finally:
        await reader.close()
    conn = psycopg.connect(postgres_url, autocommit=True)
    try:
        assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
    finally:
        conn.close()
