import importlib.util
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from agent_chassis.manifest.schema import RoleManifest
from agent_chassis.packs.base import (
    CapabilityPack,
    GuardrailPack,
    OutputGuardrailPack,
    PackConfig,
)
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

ENTRY_POINT_GROUP = "agent_chassis.packs"


class PackError(ValueError):
    """A pack reference could not be resolved or its config is invalid."""


@dataclass(frozen=True)
class ResolvedCapability:
    instance_id: str
    pack: CapabilityPack
    config: PackConfig


@dataclass(frozen=True)
class ResolvedGuardrail:
    pack: GuardrailPack | OutputGuardrailPack
    config: PackConfig

    @property
    def stage(self) -> Literal["input", "output"]:
        return "output" if isinstance(self.pack, OutputGuardrailPack) else "input"


@dataclass(frozen=True)
class OverrideRecord:
    """One manifest config override vs the pack default — the audit feed (D11)."""

    role: str
    kind: Literal["capability", "guardrail"]
    pack: str
    setting: str
    value: Any
    default: Any


@dataclass(frozen=True)
class ResolvedRole:
    """A manifest with every pack reference resolved and validated."""

    manifest: RoleManifest
    capabilities: dict[str, ResolvedCapability]
    guardrails: list[ResolvedGuardrail]
    overrides: list[OverrideRecord] = field(default_factory=list)
    eval_case_dirs: list[Path] = field(default_factory=list)

    @property
    def input_guardrails(self) -> list[ResolvedGuardrail]:
        return [g for g in self.guardrails if g.stage == "input"]

    @property
    def output_guardrails(self) -> list[ResolvedGuardrail]:
        return [g for g in self.guardrails if g.stage == "output"]

    def missing_secrets(
        self, environ: Mapping[str, str] | None = None
    ) -> dict[str, list[str]]:
        """Secrets the composed capability packs declare but the environment
        does not provide, keyed by capability instance id.

        ``required_secrets`` used to be display-only (it showed in /v1/self),
        so a role could start with a pack that was certain to fail on first
        use. The worker — the process that runs tools — refuses to start.
        """
        environ = os.environ if environ is None else environ
        missing: dict[str, list[str]] = {}
        for instance_id, capability in self.capabilities.items():
            absent = [
                name
                for name in capability.pack.required_secrets
                if not environ.get(name)
            ]
            if absent:
                missing[instance_id] = absent
        return missing


def _validate_config(
    pack_name: str, model: type[PackConfig], raw: dict[str, Any]
) -> PackConfig:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        paths = ", ".join(
            ".".join(str(p) for p in e["loc"]) or "<root>" for e in exc.errors()
        )
        raise PackError(
            f"config for pack {pack_name!r} failed validation ({paths}):\n{exc}"
        ) from exc


def _overrides(
    role: str,
    kind: Literal["capability", "guardrail"],
    pack_name: str,
    config: PackConfig,
) -> list[OverrideRecord]:
    """Fields explicitly set in the manifest that differ from pack defaults."""
    defaults = type(config)()
    return [
        OverrideRecord(
            role=role,
            kind=kind,
            pack=pack_name,
            setting=name,
            value=getattr(config, name),
            default=getattr(defaults, name),
        )
        for name in config.model_fields_set
        if getattr(config, name) != getattr(defaults, name)
    ]


class PackRegistry:
    """Discovers and resolves packs.

    Packs register explicitly (in-repo roles) or via the
    ``agent_chassis.packs`` entry-point group (third-party packs).
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityPack] = {}
        # Input and output guardrails share one namespace: the manifest names
        # a guardrail by pack name alone, so the names must be unambiguous.
        self._guardrails: dict[str, GuardrailPack | OutputGuardrailPack] = {}

    def register(
        self,
        pack: CapabilityPack | GuardrailPack | OutputGuardrailPack,
        *,
        replace: bool = False,
    ) -> None:
        store: dict[str, Any]
        if isinstance(pack, CapabilityPack):
            store = self._capabilities
        elif isinstance(pack, GuardrailPack | OutputGuardrailPack):
            store = self._guardrails
        else:
            raise PackError(
                f"{type(pack).__name__} is not a CapabilityPack, GuardrailPack"
                " or OutputGuardrailPack"
            )
        if pack.name in store:
            if not replace:
                raise PackError(f"pack {pack.name!r} is already registered")
            # Role-bundle packs may shadow a builtin of the same name: a role
            # that ships its own guardrails must keep working when the chassis
            # later promotes that pack to a builtin (image upgrades must not
            # break existing role bundles). Loud, never silent.
            _logger.warning(
                "role pack %r replaces the already-registered pack of the same name",
                pack.name,
            )
        store[pack.name] = pack

    def discover(self) -> None:
        """Load every pack advertised on the entry-point group."""
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            loaded = ep.load()
            pack = loaded() if isinstance(loaded, type) else loaded
            self.register(pack)

    def load_directory(self, path: Path | str) -> None:
        """Load packs from a directory of Python files (the second registration
        mechanism — e.g. a mounted role bundle).
        Each file exposes a module-level ``PACKS`` list of pack instances."""
        path = Path(path)
        if not path.is_dir():
            raise PackError(f"packs directory not found: {path}")
        for file in sorted(path.glob("*.py")):
            spec = importlib.util.spec_from_file_location(
                f"chassis_local_packs_{file.stem}", file
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            packs = getattr(module, "PACKS", None)
            if packs is None:
                raise PackError(f"{file} defines no module-level PACKS list")
            for pack in packs:
                self.register(pack, replace=True)

    def resolve(self, manifest: RoleManifest) -> ResolvedRole:
        """Validate every pack reference in the manifest, fail closed."""
        role = manifest.employee.name
        capabilities: dict[str, ResolvedCapability] = {}
        overrides: list[OverrideRecord] = []
        eval_case_dirs: list[Path] = []

        for ref in manifest.capabilities:
            pack = self._capabilities.get(ref.pack)
            if pack is None:
                raise PackError(
                    f"unknown capability pack {ref.pack!r} "
                    f"(registered: {sorted(self._capabilities)})"
                )
            config = _validate_config(ref.pack, pack.config_model, ref.config)
            capabilities[ref.instance_id] = ResolvedCapability(
                instance_id=ref.instance_id, pack=pack, config=config
            )
            overrides.extend(_overrides(role, "capability", ref.pack, config))
            cases = pack.eval_cases_dir()
            if cases is not None:
                eval_case_dirs.append(cases)

        # A gated tool name no composed pack provides gates nothing: a typo
        # here would let the real tool run with no approval at all. Fail
        # closed instead of silently dropping the gate (D7/D10).
        provided = {
            spec.name
            for capability in capabilities.values()
            for spec in capability.pack.tools(capability.config)
        }
        ungated = sorted(set(manifest.escalation.approval_required_tools) - provided)
        if ungated:
            raise PackError(
                f"escalation.approval_required_tools names tools no composed"
                f" capability provides: {ungated} (provided: {sorted(provided)})"
            )

        guardrails: list[ResolvedGuardrail] = []
        for gref in manifest.guardrails:
            gpack = self._guardrails.get(gref.pack)
            if gpack is None:
                raise PackError(
                    f"unknown guardrail pack {gref.pack!r} "
                    f"(registered: {sorted(self._guardrails)})"
                )
            gconfig = _validate_config(gref.pack, gpack.config_model, gref.config)
            guardrails.append(ResolvedGuardrail(pack=gpack, config=gconfig))
            overrides.extend(_overrides(role, "guardrail", gref.pack, gconfig))

        return ResolvedRole(
            manifest=manifest,
            capabilities=capabilities,
            guardrails=guardrails,
            overrides=overrides,
            eval_case_dirs=eval_case_dirs,
        )
