from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from agent_chassis.manifest import ManifestError, load_manifest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def lana() -> dict:
    return yaml.safe_load((FIXTURES / "lana.yaml").read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_valid_manifest_loads(tmp_path, lana):
    manifest = load_manifest(_write(tmp_path, lana))
    assert manifest.employee.name == "lana"
    assert manifest.routing.entry == "supervisor"
    assert [c.instance_id for c in manifest.capabilities] == ["billing", "support"]
    assert manifest.escalation.remind_after == timedelta(hours=4)
    assert manifest.escalation.expire_after == timedelta(days=2)


def test_unknown_field_fails_closed_with_path(tmp_path, lana):
    lana["employee"]["favourite_colour"] = "blue"
    with pytest.raises(ManifestError, match=r"employee\.favourite_colour"):
        load_manifest(_write(tmp_path, lana))


def test_unknown_nested_field_fails_closed(tmp_path, lana):
    lana["runtime"]["history_budget"]["max_tokens"] = 9000
    with pytest.raises(ManifestError, match=r"runtime\.history_budget\.max_tokens"):
        load_manifest(_write(tmp_path, lana))


def test_identifier_rules_enforced(tmp_path, lana):
    lana["employee"]["name"] = "Lana-Prod"  # uppercase + hyphen: illegal
    with pytest.raises(ManifestError, match="employee.name"):
        load_manifest(_write(tmp_path, lana))


def test_entry_must_be_declared_agent(tmp_path, lana):
    lana["routing"]["entry"] = "concierge"
    with pytest.raises(ManifestError, match="not a declared agent"):
        load_manifest(_write(tmp_path, lana))


def test_edges_must_reference_declared_agents(tmp_path, lana):
    lana["routing"]["edges"].append(["supervisor", "finance"])
    with pytest.raises(ManifestError, match="undeclared agent"):
        load_manifest(_write(tmp_path, lana))


def test_agent_capabilities_must_be_declared(tmp_path, lana):
    lana["routing"]["agents"]["billing"]["capabilities"] = ["payments"]
    with pytest.raises(ManifestError, match="undeclared capabilities"):
        load_manifest(_write(tmp_path, lana))


def test_duplicate_capability_instance_ids_rejected(tmp_path, lana):
    lana["capabilities"].append({"pack": "billing", "config": {}})
    with pytest.raises(ManifestError, match="duplicate capability instance ids"):
        load_manifest(_write(tmp_path, lana))


def test_approvers_channel_must_be_enabled(tmp_path, lana):
    lana["escalation"]["approvers_channel"] = "slack"
    with pytest.raises(ManifestError, match="not an enabled channel"):
        load_manifest(_write(tmp_path, lana))


def test_expiry_must_exceed_reminder(tmp_path, lana):
    lana["escalation"]["remind_after"] = "P3D"
    with pytest.raises(ManifestError, match="expire_after must be greater"):
        load_manifest(_write(tmp_path, lana))


def test_hop_budget_bounds(tmp_path, lana):
    lana["routing"]["max_hops_per_message"] = 0
    with pytest.raises(ManifestError, match="max_hops_per_message"):
        load_manifest(_write(tmp_path, lana))


def test_missing_file_raises_manifest_error(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "nope.yaml")


def test_non_mapping_root_rejected(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(ManifestError, match="root must be a mapping"):
        load_manifest(path)
