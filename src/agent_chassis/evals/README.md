# `evals/` — the behavioral regression contract (D17)

Evals are **chassis machinery**, not per-app scripts: a versioned case schema, a
runner that drives real conversations through a running Employee, and graders.
This is how "does this role still behave correctly" becomes a CI gate.

## Files

- [`cases.py`](cases.py) — `EvalCase` schema v1 and `load_cases()`. Cases are
  **multi-turn from day one**: `messages` is the conversation so far and its
  last message (which must be from the user) is the turn under test. A case can
  assert `expected_agent`, `expected_tools` / `forbidden_tools`,
  `expected_directive` (`reply` / `escalate`), per-guardrail `pass`/`trip` expectations, and a
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

| Variable | Default | Purpose |
|---|---|---|
| `EVAL_JUDGE_MODEL` | `openai:gpt-5.4-mini-2026-03-17` | Model for the quality grader |
| `EVAL_BEARER_TOKEN` | unset | Bearer token, needed once the Employee has `HTTP_BEARER_TOKENS` set (D21) |
| `EVAL_CONCURRENCY` | `4` | Runs in flight at once (each is its own conversation) |
| `EVAL_REPEAT` | `1` | Runs per case. Model behaviour is probabilistic, so a single run is noisy near the margin |
| `EVAL_MIN_PASS_RATE` | `1.0` | Share of a case's runs that must pass, e.g. `3` runs with `0.66` tolerates one miss |

A case whose HTTP call fails is a failed case (`execution: run failed: …`),
not an aborted run; a judge reply that cannot be read is a failed `quality`
grade.

The cases directory is **required** — the chassis must not default to any one
role's suite (zero special-casing is enforced by a test).

**CI.** [`.github/workflows/evals.yml`](../../../.github/workflows/evals.yml)
runs Lana's suite nightly on the real compose stack (3 runs per case, each must
pass in at least 2 of 3), on demand (`workflow_dispatch`), and on PRs that can
change behaviour (the Lana role, the prompt and guardrail code, the adapter, the
runner). It is advisory: it does not block merging. It needs the `OPENAI_API_KEY` repository secret. It is
deliberately **not** part of the per-PR `ci-success` gate: it makes paid model
calls and the result is probabilistic.

**Not built yet (D17):** packs can declare a bundled suite
(`CapabilityPack.eval_cases_dir()`, collected into
`ResolvedRole.eval_case_dirs`), but the runner does not read it — it runs only
the directory it is given — and only Lana has a suite.
