"""Eval runner (D17): drives real conversations through a running Employee
over the HTTP channel and grades them — the parity contract executor."""

import asyncio
import json
import os
import re
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


def _parse_verdict(raw: str) -> tuple[bool, str]:
    """(passed, reasoning) from the judge's reply.

    OpenAI's JSON mode returns bare JSON; other providers have no such
    switch and may wrap it in prose or a code fence, so fall back to the
    first {...} block. Anything unreadable is a failed grade with the reason
    stated — never an exception that takes the whole suite down.
    """
    candidates = [raw]
    block = re.search(r"\{.*\}", raw, re.DOTALL)
    if block:
        candidates.append(block.group(0))
    for candidate in candidates:
        try:
            verdict = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(verdict, dict) and isinstance(verdict.get("pass"), bool):
            return verdict["pass"], str(verdict.get("reasoning", ""))[:200]
    return False, f"judge returned no readable verdict: {raw[:120]!r}"


async def run_case(
    case: EvalCase,
    base_url: str,
    judge_model: str | None,
    *,
    token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CaseResult:
    """Drive one case over HTTP and grade it.

    Args:
        case: The case to run.
        base_url: Base URL of the running Employee's API unit.
        judge_model: Model for the quality grader; ``None`` skips it.
        token: Bearer token, needed once the Employee has HTTP_BEARER_TOKENS
            configured (D21).
        transport: httpx transport override, for tests.
    """
    conversation = str(uuid.uuid4())
    transcript: list[str] = []
    last: dict = {}
    error: str | None = None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        timeout=120, headers=headers, transport=transport
    ) as http:
        for message in case.messages:
            if message.role != "user":
                continue  # scripted assistant turns document intent; real replies stand in
            try:
                response = await http.post(
                    f"{base_url}/v1/messages",
                    json={"conversation_id": conversation, "message": message.content},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # One unreachable or failing turn fails this case, not the run.
                error = f"{type(exc).__name__}: {exc}"
                break
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
        error=error,
    )
    graders = grade_structural(case, execution)

    if (
        case.quality_prompt
        and judge_model
        and error is None
        and not last.get("guardrail_tripped")
    ):
        try:
            judged = await complete(
                judge_model,
                _JUDGE_SYSTEM,
                "Conversation:\n"
                + "\n".join(transcript)
                + f"\n\nCriteria: {case.quality_prompt}",
                force_json=True,
            )
            passed, reasoning = _parse_verdict(judged)
        except Exception as exc:  # noqa: BLE001 - provider error: fail the grade, keep going
            passed, reasoning = False, f"judge call failed: {exc}"
        graders.append(GraderResult("quality", passed, reasoning))

    return CaseResult(
        case_id=case.id,
        passed=all(g.passed for g in graders),
        graders=graders,
        transcript=transcript,
    )


async def run_suite(
    cases_path: Path,
    base_url: str,
    judge_model: str | None,
    *,
    token: str | None = None,
    concurrency: int = 4,
    repeat: int = 1,
    min_pass_rate: float = 1.0,
) -> int:
    """Run every case ``repeat`` times and gate on each case's pass rate.

    Model behaviour is probabilistic, so a single run is a coin flip near the
    margin. ``repeat`` > 1 with a ``min_pass_rate`` below 1 (say 3 runs, 0.67)
    tolerates an occasional miss while still failing a case that is broken.
    The defaults (one run, every run must pass) are the strict behaviour.
    """
    cases = load_cases(cases_path)
    # Each run is its own conversation, so they are independent; a small
    # bound keeps a suite from hammering the model provider's rate limits.
    gate = asyncio.Semaphore(max(1, concurrency))

    async def run(case: EvalCase) -> CaseResult:
        async with gate:
            return await run_case(case, base_url, judge_model, token=token)

    results = await asyncio.gather(
        *(run(case) for case in cases for _ in range(repeat))
    )
    by_case = {
        case.id: results[i * repeat : (i + 1) * repeat] for i, case in enumerate(cases)
    }

    meeting = 0
    for case_id, runs in by_case.items():
        passed = sum(r.passed for r in runs)
        ok = passed / repeat + 1e-9 >= min_pass_rate
        meeting += ok
        suffix = f" ({passed}/{repeat})" if repeat > 1 else ""
        print(f"{'PASS' if ok else 'FAIL'} {case_id}{suffix}")
        if repeat > 1 and not ok:
            print(f"     needs {min_pass_rate:.0%} of runs, got {passed / repeat:.0%}")
        for run_result in runs:
            if run_result.passed:
                continue
            for grader in run_result.graders:
                if not grader.passed:
                    print(f"     {grader.name}: {grader.detail}")
    print(f"\n{meeting}/{len(cases)} cases passed")
    return 0 if meeting == len(cases) else 1


def main() -> None:  # pragma: no cover
    # The cases dir is required: the chassis must not default to any one
    # role's suite (zero special-casing is enforced by test).
    if len(sys.argv) < 3:
        print("usage: python -m agent_chassis.evals <base_url> <cases_dir>")
        raise SystemExit(2)
    base_url = sys.argv[1]
    cases = Path(sys.argv[2])
    judge = os.environ.get("EVAL_JUDGE_MODEL", "openai:gpt-5.4-mini-2026-03-17")
    raise SystemExit(
        asyncio.run(
            run_suite(
                cases,
                base_url,
                judge,
                token=os.environ.get("EVAL_BEARER_TOKEN") or None,
                concurrency=int(os.environ.get("EVAL_CONCURRENCY", "4")),
                repeat=max(1, int(os.environ.get("EVAL_REPEAT", "1"))),
                min_pass_rate=float(os.environ.get("EVAL_MIN_PASS_RATE", "1.0")),
            )
        )
    )
