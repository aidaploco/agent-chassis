"""Compose per-agent instructions from manifest identity, pack fragments,
and the routing contract. Pure string building — vendor-free and testable
without any runtime."""

from agent_chassis.packs.registry import ResolvedRole


def _persona_block(resolved: ResolvedRole) -> str:
    employee = resolved.manifest.employee
    return (
        f"You are {employee.persona.display_name}. {employee.role}.\n"
        f"Tone: {employee.persona.tone}."
    )


def _routing_block(resolved: ResolvedRole, agent_name: str) -> str:
    routing = resolved.manifest.routing
    targets = sorted(target for source, target in routing.edges if source == agent_name)
    lines = [
        "The customer has one continuous conversation with you — transfers and"
        " departments do not exist for them; never mention them.",
    ]
    if targets:
        described = []
        for target in targets:
            capability_ids = resolved.manifest.routing.agents[target].capabilities
            hints = [
                resolved.capabilities[cid].pack.description
                or resolved.capabilities[cid].pack.name
                for cid in capability_ids
            ]
            hint = f" ({'; '.join(h for h in hints if h)})" if hints else ""
            described.append(f"{target}{hint}")
        lines.append(
            "If the request belongs to another specialist, return a route"
            f" output with target set to one of: {' | '.join(described)}."
            " Produce no user-visible text when routing."
        )
        if agent_name == routing.entry:
            lines.append(
                "You are the front door: respond directly only to greetings,"
                " small talk, and questions about yourself. Route every other"
                " request to the right specialist immediately; do not escalate"
                " yourself — specialists decide escalation."
            )
    lines.append(
        "If the request needs a human decision or exceeds your authority,"
        " return an escalate output with a concrete reason."
    )
    # Turn discipline: in a turn-based conversation a bare acknowledgment is
    # a dead turn — the customer waits and nothing happened (observed live:
    # "Thanks — I can look that up." with no tool call).
    lines.append(
        "Every reply must move the request forward: deliver the answer, use"
        " a tool now, or ask ONE concrete question. Never reply with a bare"
        " acknowledgment or an announcement of what you are about to do —"
        " do it in this same turn instead."
    )
    return "\n".join(lines)


def compose_instructions(resolved: ResolvedRole, agent_name: str) -> str:
    """The static system prompt for one manifest agent."""
    agent_spec = resolved.manifest.routing.agents[agent_name]
    fragments = [
        resolved.capabilities[capability_id].pack.instructions(
            resolved.capabilities[capability_id].config
        )
        for capability_id in agent_spec.capabilities
    ]
    blocks = [
        _persona_block(resolved),
        *fragments,
        _routing_block(resolved, agent_name),
    ]
    return "\n\n".join(block.strip() for block in blocks if block.strip())
