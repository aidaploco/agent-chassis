from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_chassis.contracts import (
    ContentPart,
    MessageEnvelope,
    OutcomeEvent,
    Reply,
    Resolution,
    Route,
    TurnDirective,
)

# D12: the outcome event is a public contract. This pins the exact v1 field
# set — a failure here means a schema change that requires an event_version
# bump and a DESIGN.md migration note.
OUTCOME_V1_FIELDS = {
    "event_version",
    "role",
    "tenant",
    "task_type",
    "conversation_id",
    "workflow_run_id",
    "resolution",
    "turns",
    "tool_calls",
    "wall_time_ms",
    "tokens_in",
    "tokens_out",
    "escalations",
    "guardrail_trips",
    "models_used",
    "started_at",
    "ended_at",
}


def _outcome(**overrides) -> OutcomeEvent:
    now = datetime.now(UTC)
    defaults = dict(
        role="lana",
        tenant="acme",
        task_type="conversation",
        conversation_id="c-1",
        workflow_run_id="r-1",
        resolution=Resolution.RESOLVED,
        turns=3,
        tool_calls=2,
        wall_time_ms=1500,
        tokens_in=900,
        tokens_out=400,
        escalations=0,
        guardrail_trips=0,
        models_used=["gpt-5.4-mini"],
        started_at=now,
        ended_at=now,
    )
    return OutcomeEvent(**{**defaults, **overrides})


def test_outcome_event_v1_field_set_is_pinned():
    assert set(OutcomeEvent.model_fields) == OUTCOME_V1_FIELDS


def test_outcome_event_round_trips():
    event = _outcome()
    assert OutcomeEvent.model_validate_json(event.model_dump_json()) == event


def test_outcome_event_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OutcomeEvent.model_validate({**_outcome().model_dump(), "surprise": 1})


def test_turn_directive_discriminates_on_kind():
    adapter = TypeAdapter(TurnDirective)
    route = adapter.validate_python(
        {"kind": "route", "target": "billing", "reason": "invoice question"}
    )
    assert isinstance(route, Route)
    assert isinstance(
        adapter.validate_python({"kind": "reply", "content": "hi"}), Reply
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "teleport", "target": "billing"})


def test_envelope_concatenates_text_parts():
    envelope = MessageEnvelope(
        sender="u-1",
        tenant="acme",
        conversation_id="c-1",
        channel="http",
        parts=[ContentPart(text="hello"), ContentPart(text="world")],
    )
    assert envelope.text == "hello\nworld"


def test_envelope_requires_at_least_one_part():
    with pytest.raises(ValidationError):
        MessageEnvelope(
            sender="u-1",
            tenant="acme",
            conversation_id="c-1",
            channel="http",
            parts=[],
        )
