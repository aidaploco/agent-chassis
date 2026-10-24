"""Capability and guardrail packs: the reuse mechanism.

A pack is a self-contained unit — tools, instruction fragments, config model,
required scopes/secrets, and its own eval suite — that roles compose via the
manifest. Packs are chassis-side and vendor-free (D2): tools are plain async
callables that the runtime adapter converts to vendor tools at build time.
"""

from agent_chassis.packs.base import (
    CapabilityPack,
    GuardrailPack,
    GuardrailVerdict,
    PackConfig,
    ToolSpec,
)
from agent_chassis.packs.registry import (
    OverrideRecord,
    PackError,
    PackRegistry,
    ResolvedCapability,
    ResolvedGuardrail,
    ResolvedRole,
)

__all__ = [
    "CapabilityPack",
    "GuardrailPack",
    "GuardrailVerdict",
    "OverrideRecord",
    "PackConfig",
    "PackError",
    "PackRegistry",
    "ResolvedCapability",
    "ResolvedGuardrail",
    "ResolvedRole",
    "ToolSpec",
]
