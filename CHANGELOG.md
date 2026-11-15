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

### Fixed

- Slack replies lost when a send failed (the retry was suppressed as a
  duplicate).
- `/v1/tasks/{id}` and `/v1/approvals` returned 500 for an unknown workflow id.
- The eval runner could not authenticate (`EVAL_BEARER_TOKEN`), aborted on one
  HTTP error, and crashed on a non-JSON judge reply.
- Lana's billing agent cancelled subscriptions without asking why in 47% of
  sampled conversations (now 0 of 30).

### Security and dependencies

- Dependency refresh clearing all 23 open Dependabot alerts (2 critical, 7
  high). `pydantic-ai-slim` is now 2.68, `openai` 3.x, `anthropic` 2.x.
- The container image runs as an unprivileged user and pins `uv`.
- Bearer tokens are compared in constant time.
- ruff 0.17 adopted; Dependabot version updates enabled.

### Known

- `TemporalAgent` is deprecated by pydantic-ai in favour of `TemporalDurability`;
  the adapter still uses it (warning only).
