from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Resolution(StrEnum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class OutcomeEvent(BaseModel):
    """One structured event per completed task/conversation (D12).

    PUBLIC CONTRACT: this schema feeds usage, cost and reliability
    metrics downstream. Breaking changes require an ``event_version`` bump and
    a migration note in DESIGN.md. The contract test pins the exact field set.
    """

    model_config = ConfigDict(extra="forbid")

    event_version: Literal[1] = 1
    role: str
    tenant: str
    task_type: str
    conversation_id: str
    workflow_run_id: str
    resolution: Resolution
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    escalations: int = Field(ge=0)
    guardrail_trips: int = Field(ge=0)
    models_used: list[str] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime
