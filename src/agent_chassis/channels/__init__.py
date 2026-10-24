"""Channel adapters (D15).

Inbound: each adapter normalizes its wire format to MessageEnvelope — the
core never learns which channel it is on. Outbound: push channels register an
OutboundSender on the worker; deliveries are at-least-once, so senders MUST
suppress duplicates by idempotency key.
"""

from agent_chassis.channels.base import OutboundDelivery, OutboundSender

__all__ = ["OutboundDelivery", "OutboundSender"]
