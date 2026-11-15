# Changelog

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
