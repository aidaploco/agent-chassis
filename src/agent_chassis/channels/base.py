from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class OutboundDelivery(BaseModel):
    """One outbound message to a push channel.

    ``key`` is the structured idempotency key (D15):
    ``{conversation_id}:{delivery_kind}:{sequence}`` — e.g. a conversation
    reply is ``{conversation_id}:turn:{n}``. Delivery activities are
    at-least-once; senders suppress duplicate keys.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    channel: str
    conversation_id: str
    text: str
    metadata: dict[str, str] = Field(default_factory=dict)


class OutboundSender(Protocol):
    """Worker-side delivery for one push channel. MUST be idempotent per
    ``delivery.key`` (D15)."""

    async def deliver(self, delivery: OutboundDelivery) -> None: ...
