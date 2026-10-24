from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContentPart(BaseModel):
    """One piece of message content. V1 is text-only but parts-shaped so
    richer channels don't force a schema break."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
    text: str


class MessageEnvelope(BaseModel):
    """Canonical inbound/outbound message. Channel adapters normalize to and
    from this; the core never learns which channel it is on."""

    model_config = ConfigDict(extra="forbid")

    sender: str
    tenant: str
    conversation_id: str
    channel: str
    parts: list[ContentPart] = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def text(self) -> str:
        """Concatenated text content, the common case for LLM input."""
        return "\n".join(p.text for p in self.parts if p.kind == "text")
