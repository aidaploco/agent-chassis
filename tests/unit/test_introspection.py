from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from agent_chassis.channels.http import create_app
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.introspection import (
    IntrospectionPack,
    build_self_description,
)
from agent_chassis.manifest import load_manifest
from agent_chassis.memory.base import MemoryEntry
from agent_chassis.memory.pack import MemoryPack
from agent_chassis.packs import CapabilityPack, PackConfig, PackRegistry, ToolSpec

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
    path = FIXTURES / "_introspection_manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    try:
        manifest = load_manifest(path)
    finally:
        path.unlink()
    return registry.resolve(manifest)


async def test_self_description_content(resolved):
    description = await build_self_description(resolved, RecentFakeSubstrate())

    assert description.employee == "lana"
    assert description.agents == ["billing", "supervisor", "support"]
    billing = next(c for c in description.capabilities if c.pack == "billing")
    assert billing.tools == ["get_invoice"]
    assert billing.required_secrets == ["BILLING_API_KEY"]  # names, not values
    assert any("ended resolved" in line for line in description.recent_outcomes)


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


def test_setup_tracing_fail_soft(monkeypatch):
    from agent_chassis.observability import setup_tracing

    setup_tracing.cache_clear()
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    assert setup_tracing() is False
    setup_tracing.cache_clear()
