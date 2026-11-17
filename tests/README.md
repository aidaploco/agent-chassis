# `tests/`

The test suite. Run everything with:

```sh
uv run pytest --cov          # CI gates at --cov-fail-under=80
```

## Layout

| Path | What it covers |
|---|---|
| [`unit/`](unit/) | Fast, no external services. Contracts, manifest validation, pack resolution, the pydantic-ai adapter, eval cases, graders and the eval runner, introspection, the builtin guardrails, the LLM helper (both providers, against mock transports), the logger, the Slack sender and approver notices, the HTTP sender identity, audit events, outcome events (golden serialization, the activity's retry semantics), the example packs, CI/Dockerfile alignment, the import-linter contract, and the **acceptance matrix**. |
| [`integration/`](integration/) | Cross-component tests: channels, the conversation and task workflows end-to-end (history compaction, schedule sync, pure tools, approver notices, audit entries) against a real Temporal dev server, and the Postgres-backed stores (pgvector memory, the audit table and the outcome table) against a throwaway container from [`integration/conftest.py`](integration/conftest.py). Without Docker the Postgres tests skip locally and fail in CI. |
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
  boundary, and messages answered in order across a forced rollover. It also
  holds the shared workflow fixtures other integration tests import.
  It also covers output guardrails: a withheld reply is erased from history
  (unless a tool ran), counts in the outcome, and a history recorded before the
  feature still replays (the `output-guardrails` patch).
  The read side has its own tests: `unit/test_records_api.py` (auth, scoping to the manifest's
  Employee whatever the caller sends, bounds, a missing store is a 503),
  `integration/test_task_list.py` (a waiting task is listed with its state; another Employee's
  queue is invisible, and the test waits for that run to be visible so absence means something)
  and the stores' `read()` tests, which also validate real rows against the API's response models.
- **`integration/test_audit_workflow.py`** and **`integration/test_audit_store.py`**
  — the audit stream (D11): an approval is not acted on until its audit row is
  written (a store outage stalls it), an outage never blocks an escalation, and
  the database itself rejects `UPDATE`, `DELETE` and `TRUNCATE`.
- **`unit/test_ci_alignment.py`** — what ships is what is tested: it fails when
  the Dockerfile's Python version or the `requires-python` floor is missing from
  the CI test matrix.

The adapter runs fine outside Temporal, so most tests drive it directly with a
`FunctionModel` rather than standing up a server.
