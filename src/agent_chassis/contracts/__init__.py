"""Chassis-owned contracts: the vendor-free types every seam speaks (D2).

Nothing in this package may import a vendor runtime SDK — enforced by the
import-linter contract in pyproject.toml.
"""

from agent_chassis.contracts.envelope import ContentPart, MessageEnvelope
from agent_chassis.contracts.history import ConversationHistory, HistoryBudget
from agent_chassis.contracts.outcome import OutcomeEvent, Resolution
from agent_chassis.contracts.turn import (
    ApprovalDecision,
    ApprovalItem,
    Escalate,
    NeedsApproval,
    Reply,
    Route,
    TurnContext,
    TurnDirective,
    TurnEvent,
    TurnRequest,
    TurnResult,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalItem",
    "ContentPart",
    "ConversationHistory",
    "Escalate",
    "HistoryBudget",
    "MessageEnvelope",
    "NeedsApproval",
    "OutcomeEvent",
    "Reply",
    "Resolution",
    "Route",
    "TurnContext",
    "TurnDirective",
    "TurnEvent",
    "TurnRequest",
    "TurnResult",
]
