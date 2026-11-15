"""Eval contract v1 (D17): case schema, loader, structural graders.

Cases are multi-turn from day one and the judge (``runner.py``) always sees
the full conversation — the two structural gaps found in the earlier
prototype's harness.
"""

from agent_chassis.evals.cases import EvalCase, EvalMessage, load_cases
from agent_chassis.evals.graders import (
    EvalExecution,
    GraderResult,
    grade_structural,
)

__all__ = [
    "EvalCase",
    "EvalExecution",
    "EvalMessage",
    "GraderResult",
    "grade_structural",
    "load_cases",
]
