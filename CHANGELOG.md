# Changelog

## 0.8.0 — 2026-11-16

Python 3.15 becomes the only supported version, tools get an idempotency key, and there
is a dev console. **Upgrade check:** installing the package from source now needs
Python 3.15 or newer (3.13 and 3.14 are refused); the container image already ran 3.15
and is unaffected. Nothing else needs changing: `/v1/self` only gains fields, and a tool
that ignores the new `idempotency_key()` behaves exactly as before.

- **A dev console (`console/`).** A Next.js app to see what the chassis does: switch
  between Employees, chat, and see under every reply the input checks, the agent (and
  hand-off), the tools and the output checks, with a role panel read from the running
  chassis. `docker-compose.console.yml` runs Lana and Nova side by side (one api and worker
  each, their own task queue and port). The browser never holds the token: the app's own
  routes add it. It is a dev tool and adds nothing to the chassis's runtime. It has no
  login of its own, so the console and every compose port bind to 127.0.0.1 only. A
  `.dockerignore` keeps `console/` (and `.env`, `.git`) out of the image build context.
- `GET /v1/self` now also returns `guardrail_details` (each guardrail with its stage,
  `input` or `output`), `entry_agent` and `agent_graph` (each agent, its capabilities and
  who it can route to). Additive: `guardrails` is unchanged.
- CI gains a `console` job (lint, typecheck, tests, build) in `ci-success`, and Dependabot
  covers `console/`.
- **Tools run at-least-once; `idempotency_key()` lets them cope.** A worker killed
  after a tool did its work but before Temporal recorded it re-runs the tool: measured
  with a ticket-creating tool, the retry made a second ticket. New
  `agent_chassis.packs.idempotency_key()` returns a key that is the same on every
  attempt of one tool call and different for every other call; a tool passes it to the
  system it writes to, or derives its record id from it. The example ticket tool now
  does, and a repeat of the kill experiment produced one ticket. The chassis does not
  enforce it: a tool that ignores the key is unchanged. Also verified: a worker killed
  mid-turn (during a guardrail check or a model request) loses no conversation (12 of 12
  recovered, in 36 to 39s), and the Anthropic provider path passes Lana's eval suite
  (17 of 17 cases, 3 runs each). See DESIGN D1 and `packs/README.md`.
- **Python 3.15 is now the only supported version.** `requires-python` is `>=3.15`
  (it was `>=3.13`), matching the container image, which already ran 3.15; CI,
  pyright and `.python-version` follow. Installing the package on 3.13 or 3.14 is
  no longer possible. The container image is unaffected.

## 0.7.0 — 2026-11-16

Adds output guardrails (D5) and fixes a slow first request after a worker start. No
upgrade steps; a role gets output guardrails by listing them in its manifest
`guardrails`.

- **Output guardrails (D5).** A new pack kind, `OutputGuardrailPack`, checks a
  final reply before it is returned (conversations and tasks). A tripped reply is
  replaced by a fixed fallback, the exchange is dropped from the model's history
  and the guardrail transcript, and the trip counts in the outcome's
  `guardrail_trips` (a task's reply carries `guardrail_tripped`). The guardrail's own
  log and span carry the reason, never the reply. Three deterministic builtins: `reply_length`,
  `secret_leak`, `prompt_leak`. Lana enables all three; no false trip in two live
  passes of its 17-case eval suite (3 runs per case), and its greeting and
  knowledge cases now assert the three pass. If a tool ran during the turn, a
  withheld reply keeps the history so the model remembers the action.
- Output checks run as a new `run_output_guardrail` activity behind the
  `output-guardrails` workflow patch (PATCHES.md): runs in flight at deploy skip
  them until they finish. Roles with no output guardrail are unaffected.
- **Fixed: the first requests after a worker start timed out.** pydantic-ai imports a
  large price table (`genai_prices.data`) the first time a workflow task runs a model
  request, and in a container that cold import exceeded Temporal's 2-second
  workflow-task limit (`TMPRL1101` deadlock errors; 7 on a fresh stack, and
  `ReadTimeout`s in the first eval run). The adapter now imports it when it loads:
  0 deadlock errors and four concurrent cold requests all answered.
- Input guardrail trips and output guardrail trips share one manifest list and one
  name space; `ResolvedRole` gains `input_guardrails` / `output_guardrails`.

## 0.6.0 — 2026-11-16

Adds the outcome table (D12). No upgrade steps: the worker creates
`chassis_outcomes` at start, with the same Postgres access it already needs for
the audit table.

- **Outcome table (D12).** Every finished conversation or task now also writes a
  row to an append-only `chassis_outcomes` table (typed columns, unique per run),
  emits a span `outcome <task_type>`, and keeps its log line and memory entry. A
  store outage is retried for about three minutes and then given up on without
  holding the workflow open (the log line has the full event for a backfill).
  The exact JSON of `OutcomeEvent` is now pinned by a golden test; the contract
  is unchanged (`event_version` 1). No read API; query the table.
- The audit and outcome stores share a pool helper: a worker now holds at most 4
  connections per store (it was 4 idle per pool), and a Postgres restart no longer
  strands the next write on a dead connection.

## 0.5.1 — 2026-11-16

A fix for a regression in 0.4.0 and 0.5.0. No upgrade steps.

### Fixed

- **The `off_topic` guardrail refused ordinary greetings.** The "quoted data"
  framing added in 0.4.0 (to stop the guardrail model obeying "write me a poem")
  made it judge messages too literally: measured on the Lana role, 42 of 120
  greetings were refused ("Hi there!" 15 of 20; "Hey, quick question" 20 of 20),
  against 0 of 120 before. The verdict is now requested as a JSON object in the
  provider's JSON mode with the message sent unwrapped, and the `off_topic` policy
  says a conversation opener passes. Measured (20 runs per message): 0 of 400
  false positives on off-topic and 0 of 200 on injection, with every off-topic
  request and injection caught, including the tag-closing attack. Live on the
  stack: 0 of 180 greetings refused. A JSON reply with no valid verdict is
  unreadable and fails closed.
- **The eval suite could not see it**, because the greeting case only checked that
  the supervisor replied, and a refusal is a reply. The greeting cases now assert
  the guardrails pass, and a case can carry its own `min_pass_rate` (they use
  `1.0`), since the suite's 2-of-3 tolerance hides a 1-in-6 refusal rate. Lana has
  17 cases.

## 0.5.0 — 2026-11-16

Adds the audit table and puts approvals on the audit store's critical path, so
read **Check before upgrading** first.

### Check before upgrading

- **A worker with `POSTGRES_URL` set now connects to it at start** to create the
  audit table, including for a memory-less role (`substrate: none`) that never
  used the database before. If it is unreachable the worker will not start.
  Without `POSTGRES_URL` it starts as before and warns that approvals,
  escalations and overrides are only logged.
- **A Postgres outage now pauses approvals.** An approval is written to the audit
  table before the workflow acts on it, and a failed write is retried, not
  skipped. (Escalations and reminders stay best-effort.)
- **The container image runs Python 3.15** (it was 3.13). CI tests both 3.13 (the
  declared floor) and 3.15.
- **Anthropic verdicts use the provider's default temperature.** The `anthropic`
  2.x SDK no longer accepts one; see Fixed.
- The `chassis_audit` table is created automatically. Nothing to run by hand.

### Added

- **Audit table (D11).** Approval decisions, escalations (and reminders) and the
  manifest overrides in force are written to an append-only `chassis_audit`
  table in the same Postgres as memory. The database enforces append-only with
  triggers (no `UPDATE`, `DELETE` or `TRUNCATE`; a superuser could still drop
  them); writes are idempotent per `(kind, dedupe_key)`, so retries and restarts
  add nothing; overrides are written once per manifest version. Verified live: a
  task approved while Postgres was down stalled, then finished with exactly one
  row when it returned. No read API yet; query the table directly (example in
  `audit/README.md`).
- **Types are checked in CI** with pyright (standard mode, `uv run pyright`).
- **CI tests Python 3.13 and 3.15**, and a test fails if the Dockerfile's Python
  version or the `requires-python` floor is missing from the matrix.
- **The live eval job also runs on PRs that can change behaviour** (the Lana
  role, the packs and adapter code, `utils/llm.py`, the runner), and is skipped on
  Dependabot PRs, which get no secrets. It stays advisory.
- Two Lana eval cases for the plan-pricing phrasings that used to fail (15 total).

### Fixed

- **Lana: plan-pricing questions no longer dead-end in billing.** "What does the
  Pro plan cost and what's included?" reached billing, which has no catalog tools
  and had no way to hand back, so it answered "I don't have the catalog".
  Measured per question (30 runs each) across nine pricing phrasings: 205/270
  correct before (one phrasing failed 29 of 30), 270/270 after. Billing and
  support now have direct edges and each says what is not theirs. The
  cancel-discipline rate is unchanged (0/30) and the live suite was 13/13 three
  times. Sampled rates, not guarantees.
- **The Anthropic path of `utils/llm.py` crashed.** `anthropic` 2.x no longer
  accepts `temperature` on `messages.create`, so every `anthropic:` guardrail or
  eval-judge call would have raised `TypeError`. The 0.4.0 dependency refresh
  caused it and nothing exercised the path. Found by the type checker; now covered
  by tests against mock transports (OpenAI too). It has still never run against
  the real Anthropic API.
- pgvector SQL is composed with `psycopg.sql` (the table name is quoted as an
  identifier), and the guardrail packs narrow their config inside `check` instead
  of breaking the base class signature (the type checker's first 15 findings).

### Not done (as of 0.5.0)

- Outcome events (D12) as a Postgres table with typed columns, plus the OTel span
  event; output validators (D5); a read API for the audit table.

## 0.4.0 — 2026-11-15

A hardening release. **Several changes make a configuration that used to start
now refuse to**, which is why this is a minor bump rather than a patch.

### Startup now fails closed (check your manifests and environment)

- `escalation.approval_required_tools` must name tools a composed capability
  provides. A misspelled name used to gate nothing, leaving the real tool
  ungated.
- `channels` accepts only `http` and `slack`, and the Slack webhook route is
  mounted only when `slack` is listed.
- `memory.substrate` accepts only `pgvector` or `none` (a typo used to run the
  role memory-less silently).
- `kpis.task_types` must list every declared task and may list only
  `conversation` and declared task names.
- The worker refuses to start when a composed pack's `required_secrets` are not
  set in its environment.
- `expected_directive: route` is no longer valid in eval cases (it could never
  pass).

### Behaviour changes

- `POST /v1/messages` takes the sender from the authenticated principal once
  `HTTP_BEARER_TOKENS` is configured; the request body's `sender` is honoured
  only in the anonymous local-dev posture (D21).
- The LLM guardrails fail closed: a verdict that is not exactly `FLAG` or `PASS`
  trips the guardrail.
- Conversation history compaction now keeps history within 1.25x
  `history_budget.max_turns` and counts a routed turn once (behind
  `workflow.patched("compaction-by-history-turns")`).
- A task whose approval expired emits `resolution: expired` rather than
  `escalated`.
- Push delivery retries 4 times, then gives up instead of retrying forever and
  blocking later turns.
- Temporal Schedules sync to the manifest on worker start: changed crons are
  updated, and schedules under the employee's id prefix that the manifest no
  longer declares are **deleted**.
- Keyword retrieval matches per term (stop words ignored) and is fused with the
  semantic arm by reciprocal rank.
- A reply the model returns as two identical text parts is sent once.
- **Escalations and approval requests are pushed to a human.** When
  `escalation.approvers_channel` is `slack` and `SLACK_APPROVERS_WEBHOOK_URL` is
  set, a conversation escalation, a task pausing for approval, a task's final
  escalation and every approval reminder send a notice (why, who with, which
  conversation or task, the pending calls, how to decide). It uses its own
  webhook, separate from the one customer replies use, and never derives a
  channel from a conversation id. With `approvers_channel: http` (pull-based) or
  no webhook, the worker now **warns at start** that nobody will be told.
  Gated by `workflow.patched("notify-approvers")`: tasks already paused when you
  upgrade will not send notices; new ones will.
- The adapter builds agents with pydantic-ai's `TemporalDurability` capability
  instead of the deprecated `TemporalAgent` wrapper. Activity names are
  unchanged, and an in-flight conversation continued across the swap.

### Fixed

- Slack replies lost when a send failed (the retry was suppressed as a
  duplicate).
- `/v1/tasks/{id}` and `/v1/approvals` returned 500 for an unknown workflow id.
- The eval runner could not authenticate (`EVAL_BEARER_TOKEN`), aborted on one
  HTTP error, and crashed on a non-JSON judge reply.
- Lana's billing agent cancelled subscriptions without asking why in 47% of
  sampled conversations (now 0 of 30).
- The worker's own INFO log lines were silently dropped in containers (it runs
  as `__main__`), including `worker started` and the `manifest_override` audit
  lines. They now appear.

### Security and dependencies

- Dependency refresh clearing all 23 open Dependabot alerts (2 critical, 7
  high). `pydantic-ai-slim` is now 2.68, `openai` 3.x, `anthropic` 2.x.
- The container image runs as an unprivileged user and pins `uv`.
- Bearer tokens are compared in constant time.
- ruff 0.17 adopted; Dependabot version updates enabled.

### CI

- A nightly job runs Lana's eval suite on the real compose stack (3 runs per
  case, 2 of 3 must pass). It needs the `OPENAI_API_KEY` repository secret and
  is advisory, not part of the `ci-success` gate.

### Not done

- The Postgres audit table (D11), output validators (D5) and a type checker.
