# `nova/` — the ops/reporting Employee

The V1 *second* role, deliberately unlike Lana: a single reporter agent that
runs on a **schedule** and asks a **human before publishing**. Its job is to
prove a very different role shape needs zero chassis changes.

## Shape

- **Runs on:** `TaskWorkflow` (D6) — scheduled/one-shot, run-to-completion,
  not a chat loop.
- **Human approval (D10):** `publish_report` is listed in the manifest's
  `escalation.approval_required_tools`, so calling it pauses the task in
  `WAITING_ON_HUMAN` until an approver signs off (with reminder + expiry
  timers). The gating is in the *manifest*, not the pack — the same reporting
  pack elsewhere could publish freely.
- **Schedule:** a `daily_ops_report` task fires at 08:00 via cron.
- **Guardrails:** the *same* builtin packs as Lana at *different* thresholds —
  this is the point of the acceptance matrix (same code, role-specific data).

## Files

- [`manifest.yaml`](manifest.yaml) — the role, including the `tasks` and
  `schedules` sections Lana doesn't use.
- [`packs/reporting.py`](packs/reporting.py) — metrics tools plus the
  approval-gated `publish_report`. Mock-backed and stateless like Lana's packs.

## Run it

```sh
ROLE_DIR=./examples/nova docker compose up --build
# kick off the task, then poll / approve:
curl -s localhost:8001/v1/tasks -H 'content-type: application/json' \
  -H 'authorization: Bearer <token>' -d '{"task":"daily_ops_report"}'
```

`/v1/tasks` and `/v1/approvals` require `HTTP_BEARER_TOKENS` to be configured
(D21) — an approval must be attributable to a real principal.
