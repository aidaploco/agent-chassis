from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_chassis.contracts.envelope import MessageEnvelope
from agent_chassis.contracts.history import ConversationHistory


class Reply(BaseModel):
    """Respond to the user; ends the routing loop for this inbound message."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["reply"] = "reply"
    content: str


class Route(BaseModel):
    """Pass the conversation to another capability agent (D4).

    Produces no user-visible text. ``reason``/``note`` are internal context
    for the target agent, never shown to the user.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["route"] = "route"
    target: str
    reason: str
    note: str | None = None


class Escalate(BaseModel):
    """Enter the human-escalation state (D10)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["escalate"] = "escalate"
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalItem(BaseModel):
    """One tool call awaiting human approval — chassis-readable (D10).

    ``call_id`` is the runtime's correlation id; the vendor-shaped call
    itself stays inside ``ConversationHistory``, so this carries only what
    approvers and the audit log need to see.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    args: str


class NeedsApproval(BaseModel):
    """The run is paused on approval-gated tool calls (D10): the workflow
    enters WAITING_ON_HUMAN and resumes with ApprovalDecisions."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["needs_approval"] = "needs_approval"
    items: list[ApprovalItem] = Field(min_length=1)


class ApprovalDecision(BaseModel):
    """A human's decision on one pending tool call, carrying the D11 audit
    fields (who / when / comment)."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    approved: bool
    approver: str
    comment: str = ""
    decided_at: datetime


TurnDirective = Annotated[
    Reply | Route | Escalate | NeedsApproval, Field(discriminator="kind")
]
"""What an agent turn decided: reply | route | escalate | needs_approval."""


class TurnEvent(BaseModel):
    """Normalized record of something the runtime did during a turn."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["model_request", "tool_call", "tool_output", "guardrail"]
    agent: str
    name: str
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TurnContext(BaseModel):
    """Identity of one turn within one conversation for one employee."""

    model_config = ConfigDict(extra="forbid")

    employee: str
    tenant: str
    conversation_id: str
    turn_number: int = Field(ge=1)


class TurnRequest(BaseModel):
    """Chassis → adapter: execute one agent turn.

    ``message`` is None for scheduled/task turns (D6), which instead carry
    ``prompt`` (first run) or ``decisions`` (resuming a WAITING_ON_HUMAN
    pause with the humans' verdicts).
    """

    model_config = ConfigDict(extra="forbid")

    agent: str
    message: MessageEnvelope | None
    history: ConversationHistory
    context: TurnContext
    routing_note: str | None = None
    prompt: str | None = None
    decisions: list[ApprovalDecision] = Field(default_factory=list)


class TurnResult(BaseModel):
    """Adapter → chassis: what the turn decided and what happened."""

    model_config = ConfigDict(extra="forbid")

    directive: TurnDirective
    history: ConversationHistory
    events: list[TurnEvent] = Field(default_factory=list)
