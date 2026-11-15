# Workflow patch ledger (D14)

Every behavior-affecting change to workflow-deterministic code ships behind
`workflow.patched("<change-slug>")`. Removal follows Temporal's two-step
(`patched` → `deprecate_patch` → delete) once old runs have drained — bounded
by the conversation idle timeout plus margin.

| Patch id | Introduced | Earliest removal | Status |
|---|---|---|---|
| `compaction-by-history-turns` | 2026-11-14 | 2026-11-21 (pre-patch runs end at their next continue-as-new or idle timeout) | active — `ConversationWorkflow._maybe_compact`: trigger on turns held in history instead of every `max_turns`-th turn |
| `notify-approvers` | 2026-11-15 | 2026-12-06 — longest `expire_after` among deployed manifests (a paused task lives that long) plus the conversation idle timeout, plus margin | active — `ConversationWorkflow._routing_loop` and `TaskWorkflow` (pause, final escalation, reminders): schedule the `notify_approvers` activity after each `record_escalation`. Runs paused before the deploy replay without it, so they stay silent until they finish. |
