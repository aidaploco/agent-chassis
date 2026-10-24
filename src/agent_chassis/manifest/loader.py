from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_chassis.manifest.schema import RoleManifest


class ManifestError(ValueError):
    """A manifest failed to load or validate. Message lists every offending
    field path — the loader fails closed on anything unrecognized (D7)."""


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        path = ".".join(str(p) for p in error["loc"]) or "<root>"
        lines.append(f"  {path}: {error['msg']}")
    return "\n".join(lines)


def load_manifest(path: Path | str) -> RoleManifest:
    """Load and validate a role manifest, failing closed on unknown fields."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(
            f"manifest root must be a mapping, got {type(raw).__name__}"
        )

    try:
        return RoleManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(
            f"manifest validation failed ({path}):\n{_format_errors(exc)}"
        ) from exc
