"""Outcome events (D12): the public contract's exact serialization, how the
emit_outcome activity behaves across retries, its span, and the worker builder."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_chassis.config import ChassisSettings
from agent_chassis.contracts import OutcomeEvent, Resolution
from agent_chassis.durable import activities as activities_module
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.runtime_context import RoleRuntime
from agent_chassis.outcomes import PgOutcomeSink
from agent_chassis.role_loader import load_role
from agent_chassis.worker import build_outcome_sink

EXAMPLES = Path(__file__).parents[2] / "examples"


def _event(**fields) -> OutcomeEvent:
    return OutcomeEvent.model_validate(
        {
            "role": "lana",
            "tenant": "acme",
            "task_type": "conversation",
            "conversation_id": "c-1",
            "workflow_run_id": "r-1",
            "resolution": Resolution.RESOLVED,
            "turns": 3,
            "tool_calls": 2,
            "wall_time_ms": 2000,
            "tokens_in": 900,
            "tokens_out": 400,
            "escalations": 0,
            "guardrail_trips": 0,
            "models_used": ["gpt-5.4-mini"],
            "started_at": datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC),
            "ended_at": datetime(2026, 1, 2, 9, 0, 2, tzinfo=UTC),
            **fields,
        }
    )


def test_outcome_event_v1_golden_serialization():
    """D12: the outcome event is a public contract, so its exact JSON is pinned,
    not only its field set. A diff here is a schema change: it needs an
    event_version bump and a migration note in DESIGN.md."""
    assert _event().model_dump_json() == (
        '{"event_version":1,"role":"lana","tenant":"acme","task_type":"conversation",'
        '"conversation_id":"c-1","workflow_run_id":"r-1","resolution":"resolved",'
        '"turns":3,"tool_calls":2,"wall_time_ms":2000,"tokens_in":900,"tokens_out":400,'
        '"escalations":0,"guardrail_trips":0,"models_used":["gpt-5.4-mini"],'
        '"started_at":"2026-01-02T09:00:00Z","ended_at":"2026-01-02T09:00:02Z"}'
    )


class Sink:
    def __init__(self, *, returns: bool = True, error: Exception | None = None):
        self.events: list[OutcomeEvent] = []
        self._returns, self._error = returns, error

    async def record(self, event: OutcomeEvent) -> bool:
        if self._error:
            raise self._error
        self.events.append(event)
        return self._returns


class Substrate:
    def __init__(self) -> None:
        self.recorded: list[tuple] = []

    async def record(self, kind, content, metadata=None, *, source, author) -> str:
        self.recorded.append((kind, content, source, author))
        return "id"

    async def retrieve(self, query, limit=5, kinds=None):
        return []

    async def recent(self, kinds=None, limit=5):
        return []

    async def ping(self) -> bool:
        return True


def _activities(sink, substrate=None) -> ChassisActivities:
    settings = ChassisSettings(
        _env_file=None,
        manifest_path=str(EXAMPLES / "lana" / "manifest.yaml"),
        packs_dir=str(EXAMPLES / "lana" / "packs"),
    )
    return ChassisActivities(
        RoleRuntime(
            resolved=load_role(settings),
            adapter=None,  # type: ignore[arg-type]
            handle=None,  # type: ignore[arg-type]
            substrate=substrate,
            outcomes=sink,
        )
    )


async def test_first_attempt_logs_the_whole_event_writes_the_row_then_the_memory_entry(
    caplog,
):
    sink, memory = Sink(), Substrate()
    activities = _activities(sink, memory)

    with caplog.at_level(logging.INFO, logger="agent_chassis"):
        await activities.emit_outcome(_event())

    assert sink.events == [_event()]
    assert (
        caplog.text.count("outcome_event ") == 1
        and '"workflow_run_id": "r-1"' in caplog.text
    )
    assert list(activities.emitted_outcomes) == [_event()]
    assert [m[0] for m in memory.recorded] == ["outcome"]


async def test_a_retry_does_not_repeat_the_log_line_or_the_in_process_tail(
    monkeypatch, caplog
):
    """Attempt 1 raised after logging; attempt 2 must not log (or tail) again, but
    must still write the memory entry that attempt 1 never reached."""
    monkeypatch.setattr(activities_module, "_attempt", lambda: 2)
    sink, memory = Sink(), Substrate()
    activities = _activities(sink, memory)

    with caplog.at_level(logging.INFO, logger="agent_chassis"):
        await activities.emit_outcome(_event())

    assert "outcome_event " not in caplog.text
    assert len(activities.emitted_outcomes) == 0
    assert len(sink.events) == 1 and len(memory.recorded) == 1


async def test_a_duplicate_row_does_not_duplicate_the_memory_entry():
    sink, memory = Sink(returns=False), Substrate()

    await _activities(sink, memory).emit_outcome(_event())

    assert memory.recorded == []  # the attempt that wrote the row wrote the entry


async def test_a_store_failure_raises_so_temporal_retries_and_writes_no_memory_entry():
    sink, memory = Sink(error=ConnectionError("db down")), Substrate()

    with pytest.raises(ConnectionError):
        await _activities(sink, memory).emit_outcome(_event())

    assert memory.recorded == []


async def test_without_a_store_the_event_is_logged_and_remembered_once(caplog):
    memory = Substrate()

    with caplog.at_level(logging.INFO, logger="agent_chassis"):
        await _activities(None, memory).emit_outcome(_event())

    assert caplog.text.count("outcome_event ") == 1 and len(memory.recorded) == 1


async def test_the_outcome_is_a_span_with_the_metrics_as_attributes(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(activities_module, "_tracer", provider.get_tracer("test"))

    await _activities(Sink()).emit_outcome(_event(escalations=1, guardrail_trips=2))

    (span,) = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert span.name == "outcome conversation"
    assert attributes["session.id"] == "c-1"  # groups with the conversation's turns
    assert attributes["outcome.resolution"] == "resolved"
    assert (attributes["outcome.tokens_in"], attributes["outcome.tokens_out"]) == (
        900,
        400,
    )
    assert (
        attributes["outcome.escalations"],
        attributes["outcome.guardrail_trips"],
    ) == (1, 2)
    assert list(attributes["outcome.models_used"]) == ["gpt-5.4-mini"]  # pyright: ignore[reportArgumentType]


def test_no_database_means_no_outcome_store():
    assert build_outcome_sink(ChassisSettings(_env_file=None)) is None


def test_a_database_builds_the_postgres_outcome_store():
    settings = ChassisSettings(_env_file=None, postgres_url="postgresql://u:p@h/db")
    assert isinstance(build_outcome_sink(settings), PgOutcomeSink)
