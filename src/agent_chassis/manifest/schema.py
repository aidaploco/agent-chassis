"""Role manifest schema v1 (D7).

Fail-closed: ``extra="forbid"`` on every model, so unknown fields anywhere in
a manifest are validation errors. Identifier fields are constrained because
Temporal derives activity names from them (D1) — they are frozen once a role
is deployed; renames are new deployments.
"""

from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from agent_chassis.contracts.history import HistoryBudget

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,40}$")]
"""Naming-stable identifier: feeds Temporal activity names, frozen per deployment."""


class PersonaSpec(BaseModel):
    """How the Employee presents itself to users.

    Fields:
        display_name: The name the Employee goes by (e.g. "Alex"). May differ
            from ``employee.name``, which is the machine identifier.
        tone: A short prose description of voice/style, injected into the
            system prompt (e.g. "direct, warm, leads with the answer").
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str
    tone: str


class EmployeeSpec(BaseModel):
    """Top-level identity of the Employee.

    Fields:
        name: The machine identifier — feeds Temporal activity names, so it is
            frozen per deployment (an ``Identifier``; renames are new deployments).
        role: One-line description of what this Employee does; goes into the
            system prompt and the introspection surface.
        persona: Display name and tone (see ``PersonaSpec``).
    """

    model_config = ConfigDict(extra="forbid")

    name: Identifier
    role: str
    persona: PersonaSpec


class AgentRuntimeOverride(BaseModel):
    """Per-agent overrides of the role-level ``RuntimeSpec``.

    Every field is optional; ``None`` means "inherit the role default". Used
    for cases like giving a routing supervisor a stronger model than the leaf
    agents.

    Fields:
        model_tier: Override the abstract tier (``light``/``standard``/``frontier``)
            for this one agent.
        temperature: Override sampling temperature (0.0–2.0).
        max_output_tokens: Override the per-response token cap (≥ 1).
    """

    model_config = ConfigDict(extra="forbid")

    model_tier: Identifier | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1)


class RuntimeSpec(BaseModel):
    """Role-wide model/runtime settings, with optional per-agent overrides.

    Fields:
        model_tier: The abstract tier every agent uses by default. Mapped to a
            concrete model per deployment via ``MODEL_TIER_*`` env vars — the
            manifest never names a concrete model.
        temperature: Default sampling temperature (0.0–2.0).
        max_output_tokens: Default per-response token cap (≥ 1).
        per_agent: Per-agent overrides keyed by agent name (see
            ``AgentRuntimeOverride``).
        history_budget: When/how much history to compact (see ``HistoryBudget``
            in ``contracts/history.py``); the compaction trigger for D6a.
    """

    model_config = ConfigDict(extra="forbid")

    model_tier: Identifier
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, ge=1)
    per_agent: dict[Identifier, AgentRuntimeOverride] = Field(default_factory=dict)
    history_budget: HistoryBudget = Field(default_factory=HistoryBudget)


class CapabilityRef(BaseModel):
    """Reference to a capability pack with per-role config.

    ``config`` is structurally accepted here and validated against the pack's
    own pydantic config model at registry-resolution time (Phase 3).

    Fields:
        pack: The registered pack name to compose (e.g. ``billing``).
        id: The instance id — how agents refer to this capability and part of
            the Temporal activity name, so it is frozen per deployment.
            Defaults to ``pack`` when omitted; set it explicitly only when
            composing the same pack twice.
        config: Overrides for the pack's config model. Validated against that
            model's field bounds at resolve time; out-of-bounds values fail.
    """

    model_config = ConfigDict(extra="forbid")

    pack: Identifier
    id: Identifier | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def instance_id(self) -> str:
        return self.id or self.pack


class AgentSpec(BaseModel):
    """One node in the routing graph.

    Fields:
        capabilities: Instance ids (from ``capabilities[].id``) this agent
            hosts. An agent with an empty list is routing-only (e.g. a
            supervisor front door). Must reference declared capabilities.
    """

    model_config = ConfigDict(extra="forbid")

    capabilities: list[Identifier] = Field(default_factory=list)


class RoutingSpec(BaseModel):
    """The agent graph the chassis walks deterministically (D4).

    Fields:
        entry: The agent that receives every new conversation.
        agents: All agents keyed by name (see ``AgentSpec``).
        edges: Legal ``[from, to]`` route directives. A ``route`` to an edge
            not listed here is an error surfaced to evals, never a silent
            fallback.
        max_hops_per_message: How many edges one inbound message may follow
            before the turn is treated as over-budget (1–10, default 3).
    """

    model_config = ConfigDict(extra="forbid")

    entry: Identifier
    agents: dict[Identifier, AgentSpec]
    edges: list[tuple[Identifier, Identifier]] = Field(default_factory=list)
    max_hops_per_message: int = Field(default=3, ge=1, le=10)


class GuardrailRef(BaseModel):
    """Guardrail pack reference. ``config`` overrides are validated against
    the pack's declared bounds at registry-resolution time and every override
    is written to the audit log (D11)."""

    model_config = ConfigDict(extra="forbid")

    pack: Identifier
    config: dict[str, Any] = Field(default_factory=dict)


class MemorySpec(BaseModel):
    """Which memory substrate backs this Employee (D8).

    Fields:
        substrate: The substrate name — ``pgvector`` in V1, or ``none`` to run
            memory-less. (A future shared knowledge substrate plugs in here.)
        collection: The namespace within the substrate that isolates this
            Employee's entries.
    """

    model_config = ConfigDict(extra="forbid")

    substrate: Identifier
    collection: Identifier


class EscalationSpec(BaseModel):
    """Human-escalation and approval policy (D10).

    Fields:
        approvers_channel: Where escalation requests surface. Must be one of
            the enabled ``channels``.
        remind_after: How long to wait before re-nudging approvers (an ISO-8601
            duration in YAML, e.g. ``PT4H``).
        expire_after: How long before an undecided approval auto-denies. Must
            be greater than ``remind_after`` (validated below).
        approval_required_tools: Tool names gated on human approval; calling one
            pauses a ``TaskWorkflow`` in ``WAITING_ON_HUMAN``. Gating lives here
            in the manifest, not in the pack code.
    """

    model_config = ConfigDict(extra="forbid")

    approvers_channel: Identifier
    remind_after: timedelta
    expire_after: timedelta
    approval_required_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _expiry_after_reminder(self) -> "EscalationSpec":
        if self.expire_after <= self.remind_after:
            raise ValueError("expire_after must be greater than remind_after")
        return self


class KpiSpec(BaseModel):
    """Declares the outcome-event shapes this role emits (D12).

    Fields:
        task_types: The ``task_type`` values that appear on this role's
            ``OutcomeEvent``s (e.g. ``conversation``, or a task name like
            ``daily_ops_report``). At least one required.
    """

    model_config = ConfigDict(extra="forbid")

    task_types: list[Identifier] = Field(min_length=1)


class TaskSpec(BaseModel):
    """A named unit of scheduled/one-shot work (D6): which agent runs it and
    the standing prompt it runs with. Schedules reference tasks by name.

    Fields:
        name: The task's identifier, referenced by ``schedules[].task`` and by
            ``POST /v1/tasks``.
        agent: Which declared agent runs the task.
        prompt: The standing instruction the task runs with (a channel may
            override it per-run via ``prompt_override``).
    """

    model_config = ConfigDict(extra="forbid")

    name: Identifier
    agent: Identifier
    prompt: str


class ScheduleSpec(BaseModel):
    """A cron trigger that starts a task (D6).

    Fields:
        name: The schedule's identifier (combined with the employee name to
            form the Temporal Schedule id).
        cron: A standard cron expression (e.g. ``0 8 * * *`` for 08:00 daily).
        task: The ``tasks[].name`` to start on each firing. Must be declared.
    """

    model_config = ConfigDict(extra="forbid")

    name: Identifier
    cron: str
    task: Identifier


class RoleManifest(BaseModel):
    """A complete role — the one document that defines an Employee (D7).

    Validated fail-closed: unknown fields anywhere are errors, and the
    ``_cross_references`` validator below enforces the relationships between
    sections (routing edges reference declared agents, agents reference
    declared capabilities, the approvers channel is enabled, schedules
    reference declared tasks, …).

    Fields:
        schema_version: Must be ``1``. Bumped only on a breaking schema change.
        employee: Identity — name, role, persona (``EmployeeSpec``).
        runtime: Model tier, sampling, token caps, history budget (``RuntimeSpec``).
        capabilities: Capability packs this role composes (``CapabilityRef``).
        routing: The agent graph the chassis walks (``RoutingSpec``).
        channels: Enabled channel adapters — a subset of the registered ones.
        guardrails: Guardrail packs and their (bounds-checked) config.
        memory: Which memory substrate backs the role (``MemorySpec``).
        escalation: Human-escalation and approval policy (``EscalationSpec``).
        kpis: The outcome-event ``task_type`` values this role emits (``KpiSpec``).
        tasks: Scheduled/one-shot work units (``TaskSpec``); empty for chat-only roles.
        schedules: Cron triggers over those tasks (``ScheduleSpec``).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    employee: EmployeeSpec
    runtime: RuntimeSpec
    capabilities: list[CapabilityRef] = Field(default_factory=list)
    routing: RoutingSpec
    channels: list[Identifier] = Field(min_length=1)
    guardrails: list[GuardrailRef] = Field(default_factory=list)
    memory: MemorySpec
    escalation: EscalationSpec
    kpis: KpiSpec
    tasks: list[TaskSpec] = Field(default_factory=list)
    schedules: list[ScheduleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cross_references(self) -> "RoleManifest":
        capability_ids = [ref.instance_id for ref in self.capabilities]
        duplicates = {c for c in capability_ids if capability_ids.count(c) > 1}
        if duplicates:
            raise ValueError(f"duplicate capability instance ids: {sorted(duplicates)}")

        agents = set(self.routing.agents)
        if self.routing.entry not in agents:
            raise ValueError(
                f"routing.entry {self.routing.entry!r} is not a declared agent"
            )
        for source, target in self.routing.edges:
            if source not in agents or target not in agents:
                raise ValueError(
                    f"routing edge [{source}, {target}] references an undeclared agent"
                )

        declared = set(capability_ids)
        for agent_name, spec in self.routing.agents.items():
            unknown = [c for c in spec.capabilities if c not in declared]
            if unknown:
                raise ValueError(
                    f"agent {agent_name!r} references undeclared capabilities: {unknown}"
                )

        if self.escalation.approvers_channel not in self.channels:
            raise ValueError(
                f"escalation.approvers_channel {self.escalation.approvers_channel!r} "
                "is not an enabled channel"
            )

        task_names = [task.name for task in self.tasks]
        task_duplicates = {t for t in task_names if task_names.count(t) > 1}
        if task_duplicates:
            raise ValueError(f"duplicate task names: {sorted(task_duplicates)}")
        for task in self.tasks:
            if task.agent not in agents:
                raise ValueError(
                    f"task {task.name!r} references undeclared agent {task.agent!r}"
                )
        declared_tasks = set(task_names)
        for schedule in self.schedules:
            if schedule.task not in declared_tasks:
                raise ValueError(
                    f"schedule {schedule.name!r} references undeclared task"
                    f" {schedule.task!r}"
                )
        return self
