"""V1 acceptance matrix: two real Employees (Lana conversations, Nova
scheduled ops) on one chassis with zero special-casing — loaded through the
exact production path (role_loader + PACKS_DIR + example manifests)."""

import re
from pathlib import Path

import pytest

from agent_chassis.config import ChassisSettings
from agent_chassis.packs.guardrails import OffTopicGuardrail
from agent_chassis.role_loader import load_role

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
SRC = Path(__file__).parent.parent.parent / "src" / "agent_chassis"


def _load(role: str):
    return load_role(
        ChassisSettings(
            manifest_path=str(EXAMPLES / role / "manifest.yaml"),
            packs_dir=str(EXAMPLES / role / "packs"),
        )
    )


@pytest.fixture(scope="module")
def lana():
    return _load("lana")


@pytest.fixture(scope="module")
def nova():
    return _load("nova")


def test_same_guardrail_pack_different_thresholds(lana, nova):
    """The SAME builtin pack class runs in both roles; only manifest config
    differs — thresholds and topic are role data, not role code."""
    lana_off = next(g for g in lana.guardrails if g.pack.name == "off_topic")
    nova_off = next(g for g in nova.guardrails if g.pack.name == "off_topic")
    assert type(lana_off.pack) is type(nova_off.pack) is OffTopicGuardrail
    assert lana_off.config.threshold == 0.5
    assert nova_off.config.threshold == 0.8
    assert "customer service" in lana_off.config.topic
    assert "operations" in nova_off.config.topic


def test_threshold_overrides_are_audited(lana, nova):
    """D11: every deviation from a pack default lands in the audit feed —
    and ONLY deviations (Lana's off_topic 0.5 equals the default, so it
    rightly produces no override record)."""
    lana_injection = next(
        o
        for o in lana.overrides
        if o.pack == "prompt_injection" and o.setting == "threshold"
    )
    assert lana_injection.value == 0.7 and lana_injection.default == 0.5
    assert not [
        o for o in lana.overrides if o.pack == "off_topic" and o.setting == "threshold"
    ]
    nova_off = next(
        o for o in nova.overrides if o.pack == "off_topic" and o.setting == "threshold"
    )
    assert nova_off.value == 0.8 and nova_off.default == 0.5


def test_nova_declares_task_and_schedule(nova):
    manifest = nova.manifest
    task = manifest.tasks[0]
    assert task.name == "daily_ops_report" and task.agent == "reporter"
    assert manifest.schedules[0].task == task.name
    assert manifest.escalation.approval_required_tools == ["publish_report"]
    tool_names = {
        spec.name
        for capability in nova.capabilities.values()
        for spec in capability.pack.tools(capability.config)
    }
    assert "publish_report" in tool_names


def test_chassis_knows_no_role_names():
    """Zero special-casing, mechanically: no example role's identity appears
    anywhere in chassis source."""
    forbidden = ("lana", "nova", "zara", "brightloop", "echo_demo")
    offenders = [
        f"{path.relative_to(SRC)}: {word}"
        for path in SRC.rglob("*.py")
        for word in forbidden
        # Word-boundary match: "innovation" must not trip on "nova".
        if re.search(rf"\b{word}\b", path.read_text().lower())
    ]
    assert not offenders, offenders
