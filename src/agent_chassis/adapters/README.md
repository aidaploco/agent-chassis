# `adapters/` — runtime adapters (the vendor seam, D2)

**This is the only part of the chassis allowed to import a vendor agent SDK.**
The import-linter contract in `pyproject.toml` forbids `pydantic_ai` everywhere
*except* here. If you find yourself wanting to `import pydantic_ai` in any other
package, the answer is: put it behind this seam instead.

## The seam

Every adapter implements the `RuntimeAdapter` protocol from
[`contracts/runtime.py`](../contracts/runtime.py) — three methods:

- `build(resolved)` → a `RuntimeHandle` (once per worker process).
- `run_turn(handle, request)` → a `TurnResult` (one agent turn).
- `compact_history(handle, history, budget)` → a smaller `ConversationHistory`.

The chassis knows nothing else about the runtime. Inside a Temporal workflow,
`run_turn` may fan out into many vendor-managed activities — the chassis does
not care, which is exactly the point.

## What's here

- [`pydantic_ai/`](pydantic_ai/) — the one implemented adapter (D1). See its
  own README.

## The seam-honesty test

Could someone write an `openai-agents` adapter that runs a whole turn as a
single activity behind this same protocol, touching *no* chassis code? Yes —
and that's the test the seam is designed to pass (D2). The seam is guaranteed
at the code-and-type level; the honest caveat (also D2) is that with
`TemporalAgent` the vendor still decides activity boundaries *inside* the
workflow, so "workflows are vendor-free" is true of the code, not of the
Temporal topology.
