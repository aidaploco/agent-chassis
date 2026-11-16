"""Lana's billing pack: cancellation discipline is enforced in the handler,
not only requested in the prompt."""

import importlib.util
from pathlib import Path

import pytest

BILLING = Path(__file__).parents[2] / "examples" / "lana" / "packs" / "billing.py"


@pytest.fixture(scope="module")
def tools():
    spec = importlib.util.spec_from_file_location("lana_billing", BILLING)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pack = module.PACKS[0]
    return {t.name: t.handler for t in pack.tools(pack.config_model())}


@pytest.mark.parametrize(
    "reason",
    [
        "Customer requested cancellation",
        "customer wants to cancel",
        "The user asked to cancel their subscription",
        "cancel",
        "Cancellation requested.",
        "wants to cancel the subscription",
        "No reason given",
        "n/a",
        "unknown",
        "",
        "   ",
    ],
)
async def test_restated_request_is_not_a_reason(tools, reason):
    result = await tools["cancel_subscription"]("dana@example.com", reason)
    assert result.startswith("Not cancelled") and "what prompted" in result


@pytest.mark.parametrize(
    "reason",
    [
        "It's too expensive for our team",
        "We switched to a competitor",
        "I want to cancel because the API keeps timing out",
        "Not using it anymore — we ended the project",
        "stop charging me, the invoices are wrong",
    ],
)
async def test_a_real_reason_cancels(tools, reason):
    result = await tools["cancel_subscription"]("dana@example.com", reason)
    assert result.startswith("Subscription cancelled")
    assert reason in result


async def test_unknown_account_still_reported(tools):
    result = await tools["cancel_subscription"]("nobody@example.com", "too pricey")
    assert result.startswith("No account found")
