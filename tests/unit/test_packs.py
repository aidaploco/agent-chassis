from pathlib import Path

import pytest
import yaml
from pydantic import Field

from agent_chassis.manifest import load_manifest
from agent_chassis.packs import (
    CapabilityPack,
    GuardrailPack,
    GuardrailVerdict,
    PackConfig,
    PackError,
    PackRegistry,
    ToolSpec,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class BillingConfig(PackConfig):
    currency: str = "EUR"


class BillingPack(CapabilityPack):
    name = "billing"
    config_model = BillingConfig
    required_secrets = ("BILLING_API_KEY",)

    def instructions(self, config: BillingConfig) -> str:
        return f"You handle billing in {config.currency}."

    def tools(self, config: BillingConfig) -> list[ToolSpec]:
        async def get_invoice(invoice_id: str) -> str:
            return f"invoice {invoice_id}"

        return [ToolSpec("get_invoice", get_invoice, "Fetch an invoice")]


class SupportPack(CapabilityPack):
    name = "support"

    def instructions(self, config: PackConfig) -> str:
        return "You handle support."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


class LengthConfig(PackConfig):
    max_chars: int = Field(default=1000, ge=100, le=5000)


class LengthGuardrail(GuardrailPack):
    name = "message_length"
    config_model = LengthConfig

    async def check(
        self, message, config: LengthConfig, context=()
    ) -> GuardrailVerdict:
        return GuardrailVerdict(passed=len(message.text) <= config.max_chars)


class InjectionConfig(PackConfig):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class InjectionGuardrail(GuardrailPack):
    name = "prompt_injection"
    config_model = InjectionConfig

    async def check(self, message, config, context=()) -> GuardrailVerdict:
        return GuardrailVerdict(passed=True)


@pytest.fixture
def registry() -> PackRegistry:
    r = PackRegistry()
    for pack in (BillingPack(), SupportPack(), LengthGuardrail(), InjectionGuardrail()):
        r.register(pack)
    return r


@pytest.fixture
def lana_manifest(tmp_path):
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["capabilities"][0]["config"] = {"currency": "USD"}
    path = tmp_path / "lana.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_manifest(path)


def test_resolve_validates_and_binds_configs(registry, lana_manifest):
    resolved = registry.resolve(lana_manifest)

    billing = resolved.capabilities["billing"]
    assert billing.config.currency == "USD"
    assert billing.pack.instructions(billing.config) == "You handle billing in USD."
    assert [g.pack.name for g in resolved.guardrails] == [
        "message_length",
        "prompt_injection",
    ]


def test_overrides_recorded_for_audit(registry, lana_manifest):
    resolved = registry.resolve(lana_manifest)

    settings = {(o.kind, o.pack, o.setting): o for o in resolved.overrides}
    currency = settings[("capability", "billing", "currency")]
    assert currency.value == "USD" and currency.default == "EUR"
    threshold = settings[("guardrail", "prompt_injection", "threshold")]
    assert threshold.value == 0.8 and threshold.default == 0.5
    # message_length config equals a valid override of the default (1000);
    # explicit-but-equal values are not overrides.
    assert ("guardrail", "message_length", "max_chars") not in settings


def test_unknown_pack_fails_closed(registry, lana_manifest):
    lana_manifest.capabilities[0].pack = "payments"
    with pytest.raises(PackError, match="unknown capability pack 'payments'"):
        registry.resolve(lana_manifest)


def test_config_outside_bounds_fails(registry, lana_manifest):
    lana_manifest.guardrails[0].config["max_chars"] = 999_999  # le=5000
    with pytest.raises(PackError, match="message_length"):
        registry.resolve(lana_manifest)


def test_unknown_config_field_fails(registry, lana_manifest):
    lana_manifest.capabilities[0].config["locale"] = "en"
    with pytest.raises(PackError, match="billing"):
        registry.resolve(lana_manifest)


def test_duplicate_registration_rejected(registry):
    with pytest.raises(PackError, match="already registered"):
        registry.register(BillingPack())


def test_role_pack_may_replace_builtin(registry):
    """The role-bundle path (PACKS_DIR) may shadow a builtin of the
    same name — image upgrades that promote a pack to builtin must not break
    role configs that still ship their own copy."""
    replacement = LengthGuardrail()
    registry.register(replacement, replace=True)
    assert registry._guardrails["message_length"] is replacement


def test_non_pack_rejected(registry):
    with pytest.raises(PackError, match="neither"):
        registry.register(object())  # type: ignore[arg-type]


def test_entry_point_discovery(monkeypatch):
    class FakeEntryPoint:
        def load(self):
            return BillingPack

    monkeypatch.setattr(
        "agent_chassis.packs.registry.entry_points",
        lambda group: [FakeEntryPoint()] if group == "agent_chassis.packs" else [],
    )
    r = PackRegistry()
    r.discover()
    assert "billing" in r._capabilities
