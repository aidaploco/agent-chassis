# `evals/` — the behavioral regression contract (D17)

Evals are **chassis machinery**, not per-app scripts: a versioned case schema, a
runner that drives real conversations through a running Employee, and graders.
This is how "does this role still behave correctly" becomes a CI gate.

## Files

- [`cases.py`](cases.py) — `EvalCase` schema v1 and `load_cases()`. Cases are
  **multi-turn from day one**: `messages` is the conversation so far and its
  last message (which must be from the user) is the turn under test. A case can
  assert `expected_agent`, `expected_tools` / `forbidden_tools`,
  `expected_directive`, per-guardrail `pass`/`trip` expectations, and a
  free-text `quality_prompt` for the judge.
- [`graders.py`](graders.py) — deterministic structural graders (agent, tools,
  directive, guardrails) over an `EvalExecution` — a normalized summary of what
  happened, never vendor types. Structural failures are conclusive without a
  judge.
- [`runner.py`](runner.py) — `run_suite()`: drives each case over the HTTP
  channel against a running Employee, then grades it. The **quality grader** is
  an LLM-as-judge that sees the *full* conversation and returns a **binary
  pass/fail with reasoning stated before the verdict** (D17 amendment — numeric
  scales regress to the middle and were rejected).
- [`__main__.py`](__main__.py) — `python -m agent_chassis.evals <base_url> <cases_dir>`.

## Run it

```sh
# against a running Employee (see the repo README quickstart)
EVAL_JUDGE_MODEL=openai:gpt-5.4-mini-2026-03-17 \
  python -m agent_chassis.evals http://localhost:8001 examples/lana/evals
```

The cases directory is **required** — the chassis must not default to any one
role's suite (zero special-casing is enforced by a test). A role's suite is the
union of its packs' bundled suites plus role-level cases; CI gates on the
per-role pass rate.
