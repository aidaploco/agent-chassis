# `tests/`

The test suite. Run everything with:

```sh
uv run pytest --cov          # CI gates at --cov-fail-under=80
```

## Layout

| Path | What it covers |
|---|---|
| [`unit/`](unit/) | Fast, no external services. Contracts, manifest validation, pack resolution, the pydantic-ai adapter, eval cases and graders, introspection, the builtin guardrails, the eval runner, the Slack sender, the HTTP sender identity, the import-linter contract, and the **acceptance matrix**. |
| [`integration/`](integration/) | Cross-component tests: channels, the conversation and task workflows end-to-end (including history compaction and schedule sync) against a real Temporal dev server, and the pgvector memory substrate against a throwaway Postgres container. |
| [`fixtures/`](fixtures/) | Shared test data (e.g. `lana.yaml`). |
| [`conftest.py`](conftest.py) | Global fixtures — notably clearing the `get_settings` lru_cache around each test so monkeypatched env is read fresh. |

## Two tests worth knowing about

- **`unit/test_acceptance_matrix.py`** — the V1 acceptance property: the same
  builtin guardrail packs run at different thresholds/topics across Lana and
  Nova, overrides are audited, Nova declares its task/schedule/gated tool,
  and **zero chassis code special-cases a role name** (a test scans the chassis
  source for role names — it has caught a real offender before).
- **`integration/test_conversation_workflow.py`** — the durability-sensitive
  properties: the D13 turn queue ordering, the D3 continue-as-new rollover
  boundary, and messages answered in order across a forced rollover.

The adapter runs fine outside Temporal, so most tests drive it directly with a
`FunctionModel` rather than standing up a server.
