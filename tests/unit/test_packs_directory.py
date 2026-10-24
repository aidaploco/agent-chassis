import pytest

from agent_chassis.packs import PackError, PackRegistry

PACK_FILE = """
from agent_chassis.packs import CapabilityPack, PackConfig, ToolSpec


class GreeterPack(CapabilityPack):
    name = "greeter"

    def instructions(self, config: PackConfig) -> str:
        return "Greet people."

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        return []


PACKS = [GreeterPack()]
"""


def test_load_directory_registers_packs(tmp_path):
    (tmp_path / "greeter.py").write_text(PACK_FILE)
    registry = PackRegistry()
    registry.load_directory(tmp_path)
    assert "greeter" in registry._capabilities


def test_load_directory_requires_packs_list(tmp_path):
    (tmp_path / "empty.py").write_text("x = 1\n")
    registry = PackRegistry()
    with pytest.raises(PackError, match="no module-level PACKS"):
        registry.load_directory(tmp_path)


def test_load_directory_missing_path():
    registry = PackRegistry()
    with pytest.raises(PackError, match="not found"):
        registry.load_directory("/nonexistent/packs")
