# `durable/` — the Temporal orchestration shell (D1, D3, D6, D13)

The heart of the chassis. Everything here is **vendor-free**: the workflow
shell owns all the deterministic orchestration and reaches the agent runtime
only through the `RuntimeAdapter` seam (via the process-level `RoleRuntime`).

## The two workflows ([`workflows.py`](workflows.py))

| Workflow | Lifecycle | Used by |
|---|---|---|
| **`ConversationWorkflow`** | Long-lived; update-driven; idle timeout; continue-as-new | Lana (chat) |
| **`TaskWorkflow`** | Scheduled/one-shot; run-to-completion; can durably wait days on approval | Nova (ops report) |

`ConversationWorkflow` is the one to read first. Its `_process` method is the
spine of a turn:

```
send_message (update, D3) ─► enqueue on FIFO turn queue (D13)
                              │
   single consumer ──────────┘
      _run_guardrails (pre-flight activities, D5)  ── trip? refuse
      _routing_loop  (walk the manifest graph, D4) ── reply / route / escalate
                      (escalate: record, audit, notify approvers, then ack the user)
      _maybe_compact (history budget, D6a)       ── once history holds 1.25x max_turns
   idle timeout ─► _emit_outcome (one OutcomeEvent, D12)
```

Before every continue-as-new the workflow drains the queue **and** waits for
`all_handlers_finished`, then carries `ConversationState` in the CAN args
(D3/D6a). `TaskWorkflow` adds the `WAITING_ON_HUMAN` approval state machine:
pause on a gated tool, wait on the `approve` signal with reminder/expiry timers,
audit-log every decision *before* acting on it (D10/D11): with a database that is
an append-only row whose failed write raises, so a Postgres outage pauses
approvals rather than letting one go unrecorded. Approval pauses and reminders
also push a notice to the approvers' channel (`notify_approvers`), bounded to a
few retries so a dead webhook costs the notice and not the conversation.

## Supporting files

| File | Role |
|---|---|
| [`activities.py`](activities.py) | `ChassisActivities`: the vendor-free activities — guardrail checks, history compaction, outcome/escalation/approval emission, approver notices (`notify_approvers`, D10), memory write-back, outbound delivery. |
| [`models.py`](models.py) | Workflow-internal pydantic types: `ConversationState`/`Params`, `TaskParams`, `ApprovalSignal`/`Record`, `EscalationRecord`, `ApproverNotice`, `GuardrailCheck`/`Outcome`, the reply/status shapes. |
| [`runtime_context.py`](runtime_context.py) | `RoleRuntime`: the process-level "this worker hosts exactly one role" (D16) context that workflow code reads (set once at startup, identical across replays): the resolved role, the adapter, the push senders, the approvers' sender, the memory substrate and the audit sink. |
| [`codec.py`](codec.py) | `EncryptionCodec`: AES-256-GCM payload encryption (D9) so Temporal only ever stores ciphertext. |
| [`converter.py`](converter.py) | Wires the pydantic data converter together with the codec — and **fails closed** if `TEMPORAL_PAYLOAD_CODEC_KEY` is unset (D9). |
| [`schedules.py`](schedules.py) | Syncs Temporal Schedules to the manifest's `schedules` on every worker start: creates, updates changed crons, and deletes ones the manifest no longer declares. |

## Determinism notes

Workflow code must be replay-deterministic. Two consequences you'll see in the
code: (1) `with workflow.unsafe.imports_passed_through():` guards imports that
the sandbox would otherwise reject; (2) OTel trace context is captured per
queued turn and re-attached around processing (D19) — because the D13 queue
decouples update handling from turn processing, without this every turn's spans
would land in whichever trace was attached last. Behavior-changing edits to
this code ship behind `workflow.patched()` (D14; ledger in `PATCHES.md`, which
currently lists `compaction-by-history-turns` and `notify-approvers`). A change
that only alters an activity's input payload (not which commands the workflow
issues) does not need one.
