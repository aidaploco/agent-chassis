from dataclasses import dataclass, field

from agent_chassis.evals.cases import EvalCase


@dataclass(frozen=True)
class EvalExecution:
    """What actually happened when the case's final user message was run.

    Produced by the eval runner driving the chassis (arrives with M3.5);
    graders only ever see this normalized summary, never vendor types.
    """

    final_agent: str
    directive: str
    final_output: str
    tools_called: list[str] = field(default_factory=list)
    guardrails_tripped: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class GraderResult:
    name: str
    passed: bool
    detail: str


def grade_structural(case: EvalCase, execution: EvalExecution) -> list[GraderResult]:
    """Deterministic graders: agent, tools, directive, guardrail expectations.

    The quality grader (LLM-as-judge over the full conversation, D17) is
    separate — structural failures don't need a judge to be conclusive.
    """
    results: list[GraderResult] = []

    if execution.error is not None:
        return [GraderResult("execution", False, f"run failed: {execution.error}")]

    if case.expected_agent is not None:
        results.append(
            GraderResult(
                "agent",
                execution.final_agent == case.expected_agent,
                f"expected {case.expected_agent!r}, got {execution.final_agent!r}",
            )
        )

    if case.expected_directive is not None:
        results.append(
            GraderResult(
                "directive",
                execution.directive == case.expected_directive,
                f"expected {case.expected_directive!r}, got {execution.directive!r}",
            )
        )

    if case.expected_tools is not None:
        missing = [t for t in case.expected_tools if t not in execution.tools_called]
        results.append(
            GraderResult(
                "tools",
                not missing,
                f"missing {missing}" if missing else f"called {execution.tools_called}",
            )
        )

    if case.forbidden_tools is not None:
        used = [t for t in case.forbidden_tools if t in execution.tools_called]
        results.append(
            GraderResult(
                "forbidden_tools",
                not used,
                f"called forbidden {used}" if used else "none called",
            )
        )

    for guardrail, expectation in case.guardrail_expectations.items():
        tripped = guardrail in execution.guardrails_tripped
        expected_trip = expectation == "trip"
        results.append(
            GraderResult(
                f"guardrail:{guardrail}",
                tripped == expected_trip,
                f"expected {expectation}, {'tripped' if tripped else 'passed'}",
            )
        )

    return results
