"""Self-description: an Employee can answer "what can you do and how have you been
performing" — exposed as an API endpoint and as a tool it can call on itself.
"""

from pydantic import BaseModel, ConfigDict, Field

from agent_chassis.durable.runtime_context import get_role_runtime
from agent_chassis.memory.base import MemorySubstrate
from agent_chassis.packs.base import CapabilityPack, PackConfig, ToolSpec
from agent_chassis.packs.registry import ResolvedRole


class CapabilityDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: str
    instance_id: str
    tools: list[str]
    required_secrets: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)


class SelfDescription(BaseModel):
    """Manifest view with secrets redacted by construction: manifests hold
    no secret values (secrets are env/Secret-delivered), only requirement
    declarations — which is exactly what this exposes."""

    model_config = ConfigDict(extra="forbid")

    employee: str
    role: str
    display_name: str
    tone: str
    model_tier: str
    channels: list[str]
    agents: list[str]
    capabilities: list[CapabilityDescription]
    guardrails: list[str]
    recent_outcomes: list[str] = Field(default_factory=list)


async def build_self_description(
    resolved: ResolvedRole,
    substrate: MemorySubstrate | None,
    recent_limit: int = 5,
) -> SelfDescription:
    manifest = resolved.manifest
    recent: list[str] = []
    if substrate is not None:
        try:
            entries = await substrate.recent(kinds=["outcome"], limit=recent_limit)
            recent = [f"{e.created_at:%Y-%m-%d %H:%M} {e.content}" for e in entries]
        except Exception:  # introspection must not fail on a sick substrate
            recent = ["(outcome history unavailable)"]

    return SelfDescription(
        employee=manifest.employee.name,
        role=manifest.employee.role,
        display_name=manifest.employee.persona.display_name,
        tone=manifest.employee.persona.tone,
        model_tier=manifest.runtime.model_tier,
        channels=list(manifest.channels),
        agents=sorted(manifest.routing.agents),
        capabilities=[
            CapabilityDescription(
                pack=cap.pack.name,
                instance_id=cap.instance_id,
                tools=[t.name for t in cap.pack.tools(cap.config)],
                required_secrets=list(cap.pack.required_secrets),
                required_scopes=list(cap.pack.required_scopes),
            )
            for cap in resolved.capabilities.values()
        ],
        guardrails=[g.pack.name for g in resolved.guardrails],
        recent_outcomes=recent,
    )


class IntrospectionPack(CapabilityPack):
    """Chassis builtin: lets an Employee describe itself in-conversation."""

    name = "introspection"

    def instructions(self, config: PackConfig) -> str:
        return (
            "When asked what you can do, which tools you have, or how you"
            " have been performing recently, call describe_self and answer"
            " from its output."
        )

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def describe_self() -> str:
            """Describe this employee: role, capabilities, recent outcomes."""
            runtime = get_role_runtime()
            description = await build_self_description(
                runtime.resolved, runtime.substrate
            )
            return description.model_dump_json(indent=2)

        return [ToolSpec("describe_self", describe_self, "Self-description")]
