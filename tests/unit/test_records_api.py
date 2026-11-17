"""GET /v1/audit and /v1/outcomes: authenticated, scoped to THIS Employee, bounded,
and honest when the store is not there."""

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from agent_chassis.channels.http import create_app
from agent_chassis.config import get_settings
from agent_chassis.manifest import RoleManifest
from agent_chassis.packs import CapabilityPack, PackConfig, PackRegistry, ToolSpec

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
AUTH = {"Authorization": "Bearer tok"}


class _Cap(CapabilityPack):
    def __init__(self, name: str) -> None:
        self.name = name  # type: ignore[misc]

    def instructions(self, config: PackConfig) -> str:
        return ""

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


class FakeAudit:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None

    async def read(self, *, employee, kind=None, limit=50):
        self.calls.append({"employee": employee, "kind": kind, "limit": limit})
        if self.error:
            raise self.error
        return [
            {
                "kind": "approval",
                "employee": employee,
                "tenant": "acme",
                "subject": "task-1",
                "actor": "aida",
                "action": "approved",
                "detail": {"tool": "publish_report"},
                "occurred_at": NOW,
                "recorded_at": NOW,
            }
        ]


class FakeOutcomes:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def read(self, *, role, limit=50):
        self.calls.append({"role": role, "limit": limit})
        return [
            {
                "event_version": 1,
                "role": role,
                "tenant": "acme",
                "task_type": "conversation",
                "conversation_id": "c-1",
                "workflow_run_id": "r-1",
                "resolution": "resolved",
                "turns": 2,
                "tool_calls": 1,
                "wall_time_ms": 1000,
                "tokens_in": 10,
                "tokens_out": 5,
                "escalations": 0,
                "guardrail_trips": 0,
                "models_used": ["m"],
                "started_at": NOW,
                "ended_at": NOW,
                "recorded_at": NOW,
            }
        ]


def _resolved():
    registry = PackRegistry()
    for name in ("billing", "support", "memory", "introspection"):
        registry.register(_Cap(name))
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"] = [c for c in data["capabilities"] if c["pack"] != "billing"]
    data["capabilities"].append({"pack": "billing", "config": {}})
    data["guardrails"] = []
    return registry.resolve(RoleManifest.model_validate(data))


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setenv("HTTP_BEARER_TOKENS", "tok:aida")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(audit=None, outcomes=None):
    async def get_client():  # never used by these routes
        raise AssertionError("unused")

    async def get_audit():
        return audit

    async def get_outcomes():
        return outcomes

    app = create_app(
        get_client, resolved=_resolved(), get_audit=get_audit, get_outcomes=get_outcomes
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_both_endpoints_require_a_valid_token(tokens):
    async with _client(FakeAudit(), FakeOutcomes()) as http:
        for path in ("/v1/audit", "/v1/outcomes"):
            assert (await http.get(path)).status_code == 401
            bad = await http.get(path, headers={"Authorization": "Bearer nope"})
            assert bad.status_code == 401
            assert (await http.get(path, headers=AUTH)).status_code == 200


async def test_endpoints_are_disabled_when_no_token_is_configured(monkeypatch):
    """Fail closed (D21): without HTTP_BEARER_TOKENS audit rows are not served at all."""
    monkeypatch.delenv("HTTP_BEARER_TOKENS", raising=False)
    get_settings.cache_clear()
    try:
        async with _client(FakeAudit(), FakeOutcomes()) as http:
            assert (await http.get("/v1/audit")).status_code == 503
            assert (await http.get("/v1/outcomes")).status_code == 503
    finally:
        get_settings.cache_clear()


async def test_reads_are_scoped_to_this_employee_whatever_the_caller_sends(tokens):
    audit, outcomes = FakeAudit(), FakeOutcomes()
    async with _client(audit, outcomes) as http:
        await http.get("/v1/audit?employee=nova&role=nova", headers=AUTH)
        await http.get("/v1/outcomes?employee=nova&role=nova", headers=AUTH)

    assert [c["employee"] for c in audit.calls] == [
        "lana"
    ]  # the manifest's, not the query's
    assert [c["role"] for c in outcomes.calls] == ["lana"]


async def test_rows_come_back_typed_and_kind_and_limit_are_passed_through(tokens):
    audit = FakeAudit()
    async with _client(audit, FakeOutcomes()) as http:
        body = (await http.get("/v1/audit?kind=approval&limit=7", headers=AUTH)).json()

    assert body[0]["actor"] == "aida" and body[0]["detail"] == {
        "tool": "publish_report"
    }
    assert audit.calls == [{"employee": "lana", "kind": "approval", "limit": 7}]


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "kind=bogus"])
async def test_bad_parameters_are_refused(tokens, query):
    async with _client(FakeAudit(), FakeOutcomes()) as http:
        assert (await http.get(f"/v1/audit?{query}", headers=AUTH)).status_code == 422


async def test_a_missing_store_is_a_clear_503_not_a_500(tokens):
    async with _client(audit=None, outcomes=None) as http:
        for path in ("/v1/audit", "/v1/outcomes"):
            response = await http.get(path, headers=AUTH)
            assert response.status_code == 503
            assert "POSTGRES_URL" in response.json()["detail"]


async def test_a_database_error_is_a_503_and_does_not_leak_it(tokens):
    audit = FakeAudit()
    audit.error = psycopg.errors.UndefinedTable(
        'relation "chassis_audit" does not exist'
    )
    async with _client(audit, FakeOutcomes()) as http:
        response = await http.get("/v1/audit", headers=AUTH)

    assert response.status_code == 503
    assert "chassis_audit" not in response.text  # the SQL error stays in the log
