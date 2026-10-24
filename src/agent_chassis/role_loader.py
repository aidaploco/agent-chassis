"""Shared role loading for both processes (API and worker): manifest →
registry (builtins + entry points + packs dir) → ResolvedRole, plus the
optional memory substrate binding. Vendor-free; the worker adds the runtime
adapter on top."""

from agent_chassis.config import ChassisSettings
from agent_chassis.introspection import IntrospectionPack
from agent_chassis.manifest import RoleManifest, load_manifest
from agent_chassis.memory.embedder import OpenAIEmbedder
from agent_chassis.memory.pack import MemoryPack
from agent_chassis.memory.pgvector import PgVectorSubstrate
from agent_chassis.packs.guardrails import BUILTIN_GUARDRAILS
from agent_chassis.packs.registry import PackRegistry, ResolvedRole
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


def load_role(settings: ChassisSettings) -> ResolvedRole:
    if not settings.manifest_path:
        raise RuntimeError(
            "MANIFEST_PATH is not set — a process hosts exactly one role"
        )
    manifest = load_manifest(settings.manifest_path)
    registry = PackRegistry()
    registry.register(MemoryPack())  # chassis builtins (D8, introspection)
    registry.register(IntrospectionPack())
    for guardrail in BUILTIN_GUARDRAILS:  # reference guardrail trio (D5)
        registry.register(guardrail())
    registry.discover()
    if settings.packs_dir:
        registry.load_directory(settings.packs_dir)
    return registry.resolve(manifest)


def build_substrate(
    settings: ChassisSettings, manifest: RoleManifest
) -> PgVectorSubstrate | None:
    """Returns an unstarted substrate; callers await .start() themselves."""
    if manifest.memory.substrate != "pgvector":
        return None
    if not settings.postgres_url:
        _logger.warning(
            "manifest requests pgvector memory but POSTGRES_URL is unset —"
            " running memory-less"
        )
        return None
    return PgVectorSubstrate(
        settings.postgres_url, manifest.memory.collection, OpenAIEmbedder()
    )
