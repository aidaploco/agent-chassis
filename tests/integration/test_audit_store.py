"""The audit store against a real Postgres (D11): idempotent appends, and
append-only enforced by the database rather than by convention."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from agent_chassis.audit import (
    AuditEvent,
    PgAuditSink,
    manifest_digest,
    record_overrides,
)
from agent_chassis.config import ChassisSettings
from agent_chassis.role_loader import load_role

EXAMPLES = Path(__file__).parents[2] / "examples"


def _event(**fields) -> AuditEvent:
    return AuditEvent.model_validate(
        {
            "kind": "approval",
            "employee": "nova",
            "tenant": "acme",
            "subject": "task-1",
            "actor": "aida",
            "action": "approved",
            "detail": {"tool": "publish_report", "comment": "ship it"},
            "dedupe_key": f"task-1:{uuid.uuid4().hex[:6]}",
            "occurred_at": datetime(2026, 1, 2, 9, 30, tzinfo=UTC),
            **fields,
        }
    )


@pytest.fixture
async def sink(postgres_url):
    store = PgAuditSink(postgres_url, table=f"audit_{uuid.uuid4().hex[:8]}")
    await store.start()
    yield store
    await store.close()


async def test_event_round_trips_with_its_actor_and_detail(sink):
    await sink.record(_event(dedupe_key="task-1:call_1"))

    (row,) = await sink.recent()

    assert (row["kind"], row["actor"], row["action"]) == (
        "approval",
        "aida",
        "approved",
    )
    assert row["employee"] == "nova" and row["subject"] == "task-1"
    assert row["detail"] == {"tool": "publish_report", "comment": "ship it"}
    assert row["occurred_at"] == datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    assert row["recorded_at"] >= row["occurred_at"]  # the store stamps its own time


async def test_a_duplicate_is_a_no_op(sink):
    event = _event(dedupe_key="task-1:call_1")

    assert await sink.record(event) is True
    assert await sink.record(event) is False  # an activity retry, or a restart
    assert len(await sink.recent()) == 1


async def test_the_dedupe_key_is_scoped_to_the_kind(sink):
    await sink.record(_event(kind="approval", dedupe_key="k"))
    await sink.record(_event(kind="escalation", action="escalated", dedupe_key="k"))

    assert {r["kind"] for r in await sink.recent()} == {"approval", "escalation"}


async def test_concurrent_duplicates_write_exactly_one_row(sink):
    event = _event(dedupe_key="task-1:call_1")

    results = await asyncio.gather(*(sink.record(event) for _ in range(10)))

    assert results.count(True) == 1
    assert len(await sink.recent()) == 1


async def test_the_table_is_append_only_at_the_database(sink, postgres_url):
    """Neither the application nor an operator with a normal login can rewrite
    history: the triggers reject UPDATE, DELETE and TRUNCATE."""
    await sink.record(_event(dedupe_key="task-1:call_1"))
    table = sink._table

    with psycopg.connect(postgres_url, autocommit=True) as conn:
        for statement in (
            f"UPDATE {table} SET actor = 'mallory'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(statement)  # pyright: ignore[reportArgumentType]

    (row,) = await sink.recent()
    assert row["actor"] == "aida"  # untouched


async def test_start_is_idempotent_and_safe_to_run_concurrently(postgres_url):
    """Several worker replicas start at once; the DDL must not race or error."""
    table = f"audit_{uuid.uuid4().hex[:8]}"
    stores = [PgAuditSink(postgres_url, table=table) for _ in range(4)]
    try:
        await asyncio.gather(*(s.start() for s in stores))
        await stores[0].start()  # and again
        await stores[0].record(_event(dedupe_key="k"))
        assert len(await stores[1].recent()) == 1
    finally:
        await asyncio.gather(*(s.close() for s in stores))


async def test_a_hostile_table_name_is_quoted_not_executed(postgres_url):
    sentinel = f"sentinel_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(f"CREATE TABLE {sentinel} (id int)")  # pyright: ignore[reportArgumentType]
        store = PgAuditSink(postgres_url, table=f'evil"; DROP TABLE {sentinel}; --')
        await store.start()
        try:
            await store.record(_event(dedupe_key="k"))
            assert len(await store.recent()) == 1
        finally:
            await store.close()
        assert conn.execute(f"SELECT count(*) FROM {sentinel}").fetchone() == (0,)  # pyright: ignore[reportArgumentType]


async def test_overrides_are_recorded_once_per_manifest_version(sink):
    settings = ChassisSettings(
        _env_file=None,
        manifest_path=str(EXAMPLES / "lana" / "manifest.yaml"),
        packs_dir=str(EXAMPLES / "lana" / "packs"),
    )
    resolved = load_role(settings)
    digest = manifest_digest(settings.manifest_path or "")

    first = await record_overrides(
        sink, resolved, tenant="acme", manifest_sha256=digest
    )
    restart = await record_overrides(
        sink, resolved, tenant="acme", manifest_sha256=digest
    )
    edited = await record_overrides(
        sink, resolved, tenant="acme", manifest_sha256="f" * 64
    )

    assert first == len(resolved.overrides) > 0
    assert restart == 0  # same manifest, nothing new to say
    assert edited == first  # a changed manifest is a new version of the facts

    rows = await sink.recent(kind="manifest_override", limit=50)
    threshold = next(
        r for r in rows if r["subject"] == "guardrail:prompt_injection.threshold"
    )
    assert threshold["actor"] == "manifest" and threshold["action"] == "override"
    assert threshold["detail"]["value"] == 0.7 and threshold["detail"]["default"] == 0.5
    assert len(threshold["detail"]["manifest_sha256"]) == 64
