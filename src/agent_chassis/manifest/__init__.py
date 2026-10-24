"""Role manifest schema v1 and fail-closed loader (D7)."""

from agent_chassis.manifest.loader import ManifestError, load_manifest
from agent_chassis.manifest.schema import RoleManifest

__all__ = ["ManifestError", "RoleManifest", "load_manifest"]
