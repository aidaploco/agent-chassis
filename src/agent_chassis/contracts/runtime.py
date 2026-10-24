"""The runtime adapter seam (D2).

Chassis code (workflows, channels, guardrails, memory, evals) depends on this
protocol and never on a vendor SDK. Inside a Temporal workflow, ``run_turn``
may expand into multiple vendor-managed activities — the chassis does not
care. An openai-agents adapter could implement this same protocol with the
whole turn as one activity; that is the seam-honesty test.
"""

from typing import TYPE_CHECKING, Protocol

from agent_chassis.contracts.history import ConversationHistory, HistoryBudget
from agent_chassis.contracts.turn import TurnRequest, TurnResult

if TYPE_CHECKING:
    from agent_chassis.packs.registry import ResolvedRole


class RuntimeHandle:
    """Opaque, adapter-owned product of ``RuntimeAdapter.build``."""


class RuntimeAdapter(Protocol):
    """What the chassis needs from an agent runtime — nothing more."""

    def build(self, resolved: "ResolvedRole") -> RuntimeHandle:
        """Construct runtime agents from a resolved role.

        Called once per worker process; the returned handle is reused across
        every turn.

        Args:
            resolved: The fully validated role — manifest plus resolved
                capability/guardrail packs and their config.

        Returns:
            An opaque, adapter-owned handle passed back to ``run_turn`` and
            ``compact_history``.
        """
        ...

    async def run_turn(self, handle: RuntimeHandle, request: TurnRequest) -> TurnResult:
        """Execute one agent turn.

        Inside a Temporal workflow this may expand into multiple
        vendor-managed activities — the chassis does not observe that.

        Args:
            handle: The handle returned by ``build``.
            request: What to run — the target agent, the inbound message (or a
                task prompt / resume decisions), the current history, and turn
                context. See ``TurnRequest``.

        Returns:
            The turn's ``directive`` (reply / route / escalate / needs-approval),
            the updated ``history``, and normalized ``events`` (model calls,
            tool calls, usage).
        """
        ...

    async def compact_history(
        self,
        handle: RuntimeHandle,
        history: ConversationHistory,
        budget: HistoryBudget,
    ) -> ConversationHistory:
        """Reduce history to within budget (D6a).

        The chassis decides *when* to call this (its history-budget policy);
        the adapter decides *how* (it may summarize via a model call). Invoked
        as an activity, between turns, so no concurrent turn is in flight.

        Args:
            handle: The handle returned by ``build``.
            history: The current, possibly over-budget conversation history.
            budget: The manifest's ``runtime.history_budget`` — max turns to
                keep and the token budget for any rolling summary.

        Returns:
            A history within budget (older entries may be folded into
            ``summary``).
        """
        ...
