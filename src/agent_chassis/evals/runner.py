"""Eval runner (D17): drives real conversations through a running Employee
over the HTTP channel and grades them — the parity contract executor."""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agent_chassis.evals.cases import EvalCase, load_cases
from agent_chassis.evals.graders import (
    EvalExecution,
    GraderResult,
    grade_structural,
)
from agent_chassis.utils.llm import complete

# Binary verdict, not a numeric scale (D17 amendment): LLMs are poorly
# calibrated on scales and regress to the middle. Reasoning is stated BEFORE
# the verdict so the verdict is grounded in it.
_JUDGE_SYSTEM = """You evaluate a customer-service AI Employee against the
given criteria. FAIL if any of these hold: the criteria are not met; facts
are invented rather than grounded in tools/knowledge; instructions in the
criteria are violated; the reply is repetitive or evasive. Otherwise PASS —
imperfect phrasing alone is not a failure.
Respond JSON: {"reasoning": str, "pass": bool}."""


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    graders: list[GraderResult] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)


async def run_case(
    case: EvalCase, base_url: str, judge_model: str | None
) -> CaseResult:
    conversation = str(uuid.uuid4())
    transcript: list[str] = []
    last: dict = {}
    async with httpx.AsyncClient(timeout=120) as http:
        for message in case.messages:
            if message.role != "user":
                continue  # scripted assistant turns document intent; real replies stand in
            response = await http.post(
                f"{base_url}/v1/messages",
                json={"conversation_id": conversation, "message": message.content},
            )
            response.raise_for_status()
            last = response.json()
            transcript.append(f"user: {message.content}")
            transcript.append(f"assistant({last['agent']}): {last['content']}")

    execution = EvalExecution(
        final_agent=last.get("agent", ""),
        directive="escalate" if last.get("escalated") else "reply",
        final_output=last.get("content", ""),
        tools_called=last.get("tools_called", []),
        guardrails_tripped=(
            [last["guardrail_tripped"]] if last.get("guardrail_tripped") else []
        ),
    )
    graders = grade_structural(case, execution)

    if case.quality_prompt and judge_model and not last.get("guardrail_tripped"):
        judged = await complete(
            judge_model,
            _JUDGE_SYSTEM,
            "Conversation:\n"
            + "\n".join(transcript)
            + f"\n\nCriteria: {case.quality_prompt}",
            force_json=True,
        )
        verdict = json.loads(judged or "{}")
        graders.append(
            GraderResult(
                "quality",
                bool(verdict.get("pass", False)),
                verdict.get("reasoning", "judge returned no verdict")[:200],
            )
        )

    return CaseResult(
        case_id=case.id,
        passed=all(g.passed for g in graders),
        graders=graders,
        transcript=transcript,
    )


async def run_suite(cases_path: Path, base_url: str, judge_model: str | None) -> int:
    cases = load_cases(cases_path)
    results = [await run_case(case, base_url, judge_model) for case in cases]
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"{mark} {result.case_id}")
        for grader in result.graders:
            if not grader.passed or mark == "FAIL":
                print(f"     {grader.name}: {'ok' if grader.passed else grader.detail}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


def main() -> None:  # pragma: no cover
    # The cases dir is required: the chassis must not default to any one
    # role's suite (zero special-casing is enforced by test).
    if len(sys.argv) < 3:
        print("usage: python -m agent_chassis.evals <base_url> <cases_dir>")
        raise SystemExit(2)
    base_url = sys.argv[1]
    cases = Path(sys.argv[2])
    judge = os.environ.get("EVAL_JUDGE_MODEL", "openai:gpt-5.4-mini-2026-03-17")
    raise SystemExit(asyncio.run(run_suite(cases, base_url, judge)))
