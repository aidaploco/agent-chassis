from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from agent_chassis.channels.http import create_app
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.introspection import (
    IntrospectionPack,
    SelfDescription,
    build_self_description,
)
from agent_chassis.manifest import RoleManifest
from agent_chassis.memory.base import MemoryEntry
from agent_chassis.memory.pack import MemoryPack
from agent_chassis.packs import CapabilityPack, PackConfig, PackRegistry, ToolSpec
from agent_chassis.packs.guardrails import MessageLengthGuardrail
from agent_chassis.packs.output_guardrails import ReplyLengthGuardrail

FIXTURES = Path(__file__).parent.parent / "fixtures"


class BillingPack(CapabilityPack):
    name = "billing"
    required_secrets = ("BILLING_API_KEY",)

    def instructions(self, config: PackConfig) -> str:
        return "You handle billing."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def get_invoice(invoice_id: str) -> str:
            return invoice_id

        return [ToolSpec("get_invoice", get_invoice, "Fetch an invoice")]


class SupportPack(CapabilityPack):
    name = "support"

    def instructions(self, config: PackConfig) -> str:
        return "You handle support."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


class RecentFakeSubstrate:
    async def recent(self, kinds=None, limit=5):
        return [
            MemoryEntry(
                id="o-1",
                collection="lana",
                kind="outcome",
                content="Conversation c-9 ended resolved: 4 turns, 0 escalations.",
                created_at=datetime.now(UTC),
            )
        ]

    async def record(self, kind, content, metadata=None, *, source, author) -> str:
        return "x"

    async def retrieve(self, query, limit=5, kinds=None):
        return []

    async def ping(self) -> bool:
        return True


@pytest.fixture
def resolved():
    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(SupportPack())
    registry.register(MemoryPack())
    registry.register(IntrospectionPack())

    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = []
    return registry.resolve(RoleManifest.model_validate(data))


async def test_self_description_content(resolved):
    description = await build_self_description(resolved, RecentFakeSubstrate())

    assert description.employee == "lana"
    assert description.agents == ["billing", "supervisor", "support"]
    billing = next(c for c in description.capabilities if c.pack == "billing")
    assert billing.tools == ["get_invoice"]
    assert billing.required_secrets == ["BILLING_API_KEY"]  # names, not values
    assert any("ended resolved" in line for line in description.recent_outcomes)


async def test_self_description_graph_and_guardrail_stages(resolved):
    """The console draws a turn from these: which agents can route where, and
    which side each guardrail runs on."""
    description = await build_self_description(resolved, RecentFakeSubstrate())

    assert description.entry_agent == "supervisor"
    graph = {a.name: a for a in description.agent_graph}
    assert set(graph) == {"billing", "supervisor", "support"}
    assert "billing" in graph["supervisor"].routes_to
    assert graph["billing"].capabilities == ["billing"]

    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(SupportPack())
    registry.register(MemoryPack())
    registry.register(IntrospectionPack())
    registry.register(MessageLengthGuardrail())
    registry.register(ReplyLengthGuardrail())
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = [{"pack": "message_length"}, {"pack": "reply_length"}]
    guarded = registry.resolve(RoleManifest.model_validate(data))

    described = await build_self_description(guarded, RecentFakeSubstrate())

    assert [(g.name, g.stage) for g in described.guardrail_details] == [
        ("message_length", "input"),
        ("reply_length", "output"),
    ]
    assert described.guardrails == ["message_length", "reply_length"]  # unchanged


async def test_self_description_lists_tasks_schedules_and_gated_tools():
    """The console's "run a task" and approval screens read these."""
    registry = PackRegistry()
    registry.register(BillingPack())
    registry.register(SupportPack())
    registry.register(MemoryPack())
    registry.register(IntrospectionPack())
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {}
    data["guardrails"] = []
    data["kpis"]["task_types"] = ["conversation", "nightly"]
    data["tasks"] = [
        {"name": "nightly", "agent": "billing", "prompt": "Do the nightly run."}
    ]
    data["schedules"] = [
        {"name": "nightly_run", "cron": "0 8 * * *", "task": "nightly"}
    ]
    data["escalation"]["approval_required_tools"] = ["get_invoice"]
    role = registry.resolve(RoleManifest.model_validate(data))

    described = await build_self_description(role, RecentFakeSubstrate())

    (task,) = described.tasks
    assert (task.name, task.agent, task.schedules) == (
        "nightly",
        "billing",
        ["0 8 * * *"],
    )
    assert described.approval_required_tools == ["get_invoice"]


async def test_self_description_survives_sick_substrate(resolved):
    class SickSubstrate(RecentFakeSubstrate):
        async def recent(self, kinds=None, limit=5):
            raise RuntimeError("db down")

    description = await build_self_description(resolved, SickSubstrate())
    assert description.recent_outcomes == ["(outcome history unavailable)"]


async def test_self_endpoint(resolved):
    async def get_client():  # never called by /v1/self
        raise AssertionError("unused")

    async def get_substrate():
        return RecentFakeSubstrate()

    app = create_app(get_client, resolved=resolved, get_substrate=get_substrate)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/v1/self")

    assert response.status_code == 200
    body = response.json()
    assert body["employee"] == "lana" and body["recent_outcomes"]


async def test_describe_self_tool_shows_the_model_only_the_original_fields(resolved):
    """The API's client-only fields (routing graph, guardrail stages, tasks, gated
    tools) must not reach the model: an allowlist, so a new field is hidden by default."""
    import json

    set_role_runtime(
        RoleRuntime(
            resolved=resolved,
            adapter=None,  # type: ignore[arg-type]
            handle=None,  # type: ignore[arg-type]
            substrate=RecentFakeSubstrate(),
        )
    )
    pack = IntrospectionPack()
    (spec,) = pack.tools(pack.config_model())

    shown = set(json.loads(await spec.handler()))

    assert shown == {
        "employee",
        "role",
        "display_name",
        "tone",
        "model_tier",
        "channels",
        "agents",
        "capabilities",
        "guardrails",
        "recent_outcomes",
    }
    # ...and the API still offers the rest to its clients.
    assert {"guardrail_details", "entry_agent", "agent_graph", "tasks"} <= set(
        SelfDescription.model_fields
    )


async def test_describe_self_tool(resolved):
    set_role_runtime(
        RoleRuntime(
            resolved=resolved,
            adapter=None,  # type: ignore[arg-type]
            handle=None,  # type: ignore[arg-type]
            substrate=RecentFakeSubstrate(),
        )
    )
    pack = IntrospectionPack()
    (spec,) = pack.tools(pack.config_model())
    output = await spec.handler()
    assert '"employee": "lana"' in output and "get_invoice" in output


def test_setup_tracing_fail_soft(monkeypatch, tmp_path):
    from agent_chassis.observability import setup_tracing

    # Settings also read ./.env; run from an empty dir so a developer's local
    # tracing endpoint can't flip this test.
    monkeypatch.chdir(tmp_path)
    setup_tracing.cache_clear()
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert setup_tracing() is False
    setup_tracing.cache_clear()


def test_trace_endpoint_accepts_standard_and_legacy_names(monkeypatch):
    from agent_chassis.config import ChassisSettings

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phx/v1/traces")
    assert (
        ChassisSettings(_env_file=None).otlp_traces_endpoint == "http://phx/v1/traces"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://lf/v1/traces")
    assert (
        ChassisSettings(_env_file=None).otlp_traces_endpoint == "https://lf/v1/traces"
    )


def test_parse_headers():
    from agent_chassis.observability import _parse_headers

    assert _parse_headers(None) is None
    assert _parse_headers("") is None
    assert _parse_headers("Authorization=tok=en, x-a=b") == {
        "Authorization": "tok=en",
        "x-a": "b",
    }
