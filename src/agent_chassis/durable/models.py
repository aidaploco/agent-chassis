from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_chassis.contracts import ApprovalItem, ConversationHistory, TurnEvent


class ConversationState(BaseModel):
    """Carried through continue-as-new (D3/D6a)."""

    model_config = ConfigDict(extra="forbid")

    history: ConversationHistory
    current_agent: str
    # Chassis-owned rolling window of recent exchange lines (oldest first,
    # capped in the workflow). Distinct from `history`, which is the
    # adapter's opaque vendor transcript — this one exists so guardrails
    # can judge a message in conversational context (bare "dana@example.com"
    # is on-topic when the assistant just asked for an email).
    transcript: list[str] = Field(default_factory=list)
    turn_number: int = 0
    started_at: datetime
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    escalations: int = 0
    guardrail_trips: int = 0
    models_used: list[str] = Field(default_factory=list)


class ConversationParams(BaseModel):
    """Start arguments for ConversationWorkflow."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    tenant: str
    channel: str
    idle_timeout_seconds: int = 3600
    # Test hook for the CAN rollover boundary (D3); production rollovers use
    # is_continue_as_new_suggested().
    max_turns_per_run: int | None = None
    resume: ConversationState | None = None


class TurnReply(BaseModel):
    """What the send_message update returns to the channel."""

    model_config = ConfigDict(extra="forbid")

    content: str
    agent: str
    turn_number: int
    escalated: bool = False
    guardrail_tripped: str | None = None
    events: list[TurnEvent] = Field(default_factory=list)


class GuardrailCheck(BaseModel):
    """Input to the run_guardrail activity."""

    model_config = ConfigDict(extra="forbid")

    guardrail: str
    sender: str
    tenant: str
    conversation_id: str
    channel: str
    text: str
    # Recent conversation lines (oldest first) so LLM guardrails judge the
    # message in context instead of in isolation.
    context: list[str] = Field(default_factory=list)


class GuardrailOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guardrail: str
    passed: bool
    reason: str | None = None


class EscalationRecord(BaseModel):
    """Audit-stream entry for an escalation (D10/D11)."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    tenant: str
    agent: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class TaskParams(BaseModel):
    """Start arguments for TaskWorkflow (D6). ``task`` names a manifest
    task; ``prompt_override`` lets a channel run it with an ad-hoc prompt."""

    model_config = ConfigDict(extra="forbid")

    task: str
    tenant: str
    prompt_override: str | None = None


class ApprovalSignal(BaseModel):
    """Inbound approver decision (D10 signal). ``call_id`` None decides
    every still-pending call at once."""

    model_config = ConfigDict(extra="forbid")

    call_id: str | None = None
    approved: bool
    approver: str
    comment: str = ""


class ApprovalRecord(BaseModel):
    """D11 audit entry: one human decision on one gated tool call —
    workflow id, decision, who / when / comment. Written via activity
    BEFORE the workflow acts on the decision."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    task: str
    tenant: str
    call_id: str
    tool: str
    approved: bool
    approver: str
    comment: str
    decided_at: datetime


class TaskStatus(BaseModel):
    """Query result for a running/finished task."""

    model_config = ConfigDict(extra="forbid")

    task: str
    state: Literal["running", "waiting_on_human", "failed"]
    pending: list[ApprovalItem] = Field(default_factory=list)
    error: str | None = None


class TaskReply(BaseModel):
    """What TaskWorkflow.run returns."""

    model_config = ConfigDict(extra="forbid")

    task: str
    content: str
    agent: str
    escalated: bool = False
    approvals: int = 0
    denials: int = 0
    expired: int = 0
    events: list[TurnEvent] = Field(default_factory=list)
