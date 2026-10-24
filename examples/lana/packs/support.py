"""Support capability. Company knowledge lives HERE, once (persona/facts
drift between agents is structurally impossible: one pack owns the facts)."""

import random

from agent_chassis.packs import CapabilityPack, PackConfig, ToolSpec

_KNOWLEDGE = """
=== BRIGHTLOOP KNOWLEDGE ===
Brightloop is a webhook delivery and API orchestration platform (founded 2021,
San Francisco). It sits between upstream event sources and internal services,
ensuring reliable, ordered, observable event delivery: at-least-once delivery,
configurable retries, dead-letter queues, circuit breakers, fan-out, HMAC-SHA256
signing, replay up to 30 days. SDKs: Python, Node/TS, Go, Ruby, Java.
PRICING: Starter $49/mo (500K events, 3 endpoints, 7-day history, email support
48h, 1 seat). Pro $199/mo (5M events, unlimited endpoints, 30-day history,
custom retries, transformations, priority support 8h, 10 seats). Enterprise
custom (unlimited, 90-day history, SSO/SAML, SOC 2 reports, 99.99% SLA).
Free trial 14 days, no credit card. Annual billing: 20% discount.
SECURITY: TLS 1.2+, AES-256 at rest, SOC 2 Type II, GDPR processor (DPA
available), no HIPAA BAA, RBAC on Pro/Enterprise, SSO on Enterprise.
SLA: 99.9% uptime (Starter/Pro), 99.99% (Enterprise). Credits: 99.0-99.9% ->
10%; 95-99% -> 25%; below 95% -> 50%; request within 30 days.
=== END KNOWLEDGE ===
"""

_TICKETS: dict[str, str] = {"BL-10001": "In progress — engineering investigating"}


class SupportPack(CapabilityPack):
    name = "support"
    description = "bug reports, API errors, technical issues, product questions, pricing and plan catalog, company info, security, SLA, tickets"

    def instructions(self, config: PackConfig) -> str:
        return (
            "You are the support specialist: technical issues, product"
            " questions, company info, pricing, security, SLA.\n"
            "Platform health or API status → get_system_status. Bug or"
            " unresolved issue → create_ticket (give the customer the BL-XXXXX"
            " reference). Ticket reference given → get_ticket_status with the"
            " exact ID — never reformat it; if not found, say which ID you"
            " searched.\nFor company, pricing, security, or SLA questions,"
            " answer directly from the knowledge below. Lead with the answer;"
            " acknowledge issues honestly, no promises on fix timelines.\n" + _KNOWLEDGE
        )

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def get_system_status() -> str:
            """Current platform health."""
            return (
                "All systems operational. API p99 latency 84ms; delivery"
                " success rate (24h): 99.97%. Status page: status.example.com"
            )

        async def create_ticket(email: str, subject: str, description: str) -> str:
            """Open a support ticket; returns its BL-XXXXX reference."""
            ticket_id = f"BL-{random.randint(10000, 99999)}"
            _TICKETS[ticket_id] = "Open — awaiting triage"
            return f"Ticket {ticket_id} created for {email}: {subject}"

        async def get_ticket_status(ticket_id: str) -> str:
            """Status of an existing ticket by exact ID."""
            status = _TICKETS.get(ticket_id)
            if status is None:
                return f"No ticket found with ID {ticket_id!r}."
            return f"{ticket_id}: {status}"

        return [
            ToolSpec("get_system_status", get_system_status, "Platform health"),
            ToolSpec(
                "create_ticket",
                create_ticket,
                "Open a support ticket. Call it immediately once you have the"
                " problem description and the customer's email — do not ask"
                " for permission or confirmation first; the customer reporting"
                " the bug IS the request to file it.",
            ),
            ToolSpec("get_ticket_status", get_ticket_status, "Ticket status"),
        ]


PACKS = [SupportPack()]
