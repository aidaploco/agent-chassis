"""Audit events (D11): the event model, how approvals map to actions, and the
worker's start-up behaviour. No database: a recording fake stands in."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_chassis.audit import AuditEvent, PgAuditSink, manifest_digest
from agent_chassis.config import ChassisSettings
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.models import ApprovalRecord, EscalationRecord
from agent_chassis.durable.runtime_context import RoleRuntime
from agent_chassis.role_loader import load_role
from agent_chassis.worker import build_audit_sink

EXAMPLES = Path(__file__).parents[2] / "examples"
NOW = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.events: list[AuditEvent] = []
        self._error = error

    async def record(self, event: AuditEvent) -> bool:
        if self._error:
            raise self._error
        self.events.append(event)
        return True


def _activities(audit) -> ChassisActivities:
    settings = ChassisSettings(
        _env_file=None,
        manifest_path=str(EXAMPLES / "nova" / "manifest.yaml"),
        packs_dir=str(EXAMPLES / "nova" / "packs"),
    )
    return ChassisActivities(
        RoleRuntime(
            resolved=load_role(settings),
            adapter=None,  # type: ignore[arg-type]
            handle=None,  # type: ignore[arg-type]
            audit=audit,
        )
    )


def _approval(**fields) -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {
            "workflow_id": "task-1",
            "task": "daily_ops_report",
            "tenant": "acme",
            "call_id": "call_1",
            "tool": "publish_report",
            "approved": True,
            "approver": "aida",
            "comment": "ship it",
            "decided_at": NOW,
            **fields,
        }
    )


def _escalation(**fields) -> EscalationRecord:
    return EscalationRecord.model_validate(
        {
            "conversation_id": "conv-1",
            "tenant": "acme",
            "agent": "reporter",
            "reason": "needs a human",
            "occurred_at": NOW,
            **fields,
        }
    )


@pytest.mark.parametrize(
    ("fields", "actor", "action"),
    [
        ({"approved": True, "approver": "aida"}, "aida", "approved"),
        ({"approved": False, "approver": "bob", "comment": "no"}, "bob", "denied"),
        ({"approved": False, "approver": "system"}, "system", "expired"),
    ],
)
async def test_approval_decisions_map_to_actor_and_action(fields, actor, action):
    sink = Recorder()

    await _activities(sink).record_approval(_approval(**fields))

    (event,) = sink.events
    assert (event.kind, event.actor, event.action) == ("approval", actor, action)
    assert event.employee == "nova" and event.tenant == "acme"
    assert event.subject == "task-1" and event.dedupe_key == "task-1:call_1"
    assert event.occurred_at == NOW  # when it was decided, not when it was written
    assert event.detail["tool"] == "publish_report"


async def test_a_failed_approval_write_raises_so_temporal_retries():
    sink = Recorder(error=ConnectionError("db down"))

    with pytest.raises(ConnectionError):
        await _activities(sink).record_approval(_approval())


async def test_a_failed_escalation_write_is_logged_not_raised(caplog):
    sink = Recorder(error=ConnectionError("db down"))

    with caplog.at_level(logging.ERROR, logger="agent_chassis"):
        await _activities(sink).record_escalation(_escalation())  # must not raise

    assert "audit write failed kind=escalation" in caplog.text


async def test_escalations_and_reminders_are_labelled_differently():
    sink = Recorder()
    activities = _activities(sink)

    await activities.record_escalation(_escalation())
    await activities.record_escalation(
        _escalation(kind="reminder", occurred_at=datetime(2026, 1, 2, 10, tzinfo=UTC))
    )

    first, second = sink.events
    assert (first.action, second.action) == ("escalated", "reminded")
    assert first.dedupe_key != second.dedupe_key  # both are kept
    assert first.actor == "nova"  # the employee raised it


async def test_without_a_store_approvals_still_work_and_are_only_logged():
    activities = _activities(None)

    await activities.record_approval(_approval())

    assert len(activities.approval_records) == 1


def test_an_event_rejects_unknown_kinds_actions_and_fields():
    base = {
        "kind": "approval",
        "employee": "e",
        "tenant": "t",
        "subject": "s",
        "actor": "a",
        "action": "approved",
        "dedupe_key": "k",
        "occurred_at": NOW,
    }
    assert AuditEvent.model_validate(base).detail == {}
    for bad in ({"kind": "gossip"}, {"action": "shrugged"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            AuditEvent.model_validate({**base, **bad})


def test_no_database_means_no_audit_store_and_a_loud_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_chassis"):
        assert build_audit_sink(ChassisSettings(_env_file=None)) is None
    assert (
        "POSTGRES_URL is not set" in caplog.text
        and "no durable audit record" in caplog.text
    )


def test_a_database_builds_the_postgres_store(caplog):
    settings = ChassisSettings(_env_file=None, postgres_url="postgresql://u:p@h/db")
    with caplog.at_level(logging.WARNING, logger="agent_chassis"):
        assert isinstance(build_audit_sink(settings), PgAuditSink)
    assert not caplog.text


def test_the_manifest_digest_follows_the_file_content(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("a: 1\n")
    first = manifest_digest(manifest)
    assert first == manifest_digest(manifest) and len(first) == 64

    manifest.write_text("a: 2\n")
    assert manifest_digest(manifest) != first
