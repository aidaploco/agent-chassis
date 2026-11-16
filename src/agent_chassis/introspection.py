"""Self-description: an Employee can answer "what can you do and how have you been
performing" — exposed as an API endpoint and as a tool it can call on itself.
"""

from typing import Literal

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


class GuardrailDescription(BaseModel):
    """One configured guardrail: which side it runs on decides what a trip means
    (an input trip refuses the message; an output trip withholds the reply)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    stage: Literal["input", "output"]


class AgentDescription(BaseModel):
    """One manifest agent and the agents it may route to (D4)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    capabilities: list[str]
    routes_to: list[str]


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
    # The same guardrails with the side each runs on, and the routing graph, so a
    # client can draw how a turn flows without reading the manifest.
    guardrail_details: list[GuardrailDescription] = Field(default_factory=list)
    entry_agent: str = ""
    agent_graph: list[AgentDescription] = Field(default_factory=list)
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
        except Exception:  # noqa: BLE001 - introspection must not fail on a sick substrate
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
        guardrail_details=[
            GuardrailDescription(name=g.pack.name, stage=g.stage)
            for g in resolved.guardrails
        ],
        entry_agent=manifest.routing.entry,
        agent_graph=[
            AgentDescription(
                name=name,
                capabilities=list(spec.capabilities),
                routes_to=[to for src, to in manifest.routing.edges if src == name],
            )
            for name, spec in sorted(manifest.routing.agents.items())
        ],
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
            # The model is told what it always was. The routing graph and the guardrail
            # stages exist for the API's clients; handing them to the model would let a
            # user ask the agent how its checks are arranged.
            return description.model_dump_json(
                indent=2, exclude={"guardrail_details", "entry_agent", "agent_graph"}
            )

        return [ToolSpec("describe_self", describe_self, "Self-description")]
