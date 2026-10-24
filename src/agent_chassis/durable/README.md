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
      _maybe_compact (history budget, D6a)
   idle timeout ─► _emit_outcome (one OutcomeEvent, D12)
```

Before every continue-as-new the workflow drains the queue **and** waits for
`all_handlers_finished`, then carries `ConversationState` in the CAN args
(D3/D6a). `TaskWorkflow` adds the `WAITING_ON_HUMAN` approval state machine:
pause on a gated tool, wait on the `approve` signal with reminder/expiry timers,
audit-log every decision *before* acting on it (D10/D11).

## Supporting files

| File | Role |
|---|---|
| [`activities.py`](activities.py) | `ChassisActivities`: the vendor-free activities — guardrail checks, history compaction, outcome/escalation/approval emission, memory write-back, outbound delivery. |
| [`models.py`](models.py) | Workflow-internal pydantic types: `ConversationState`/`Params`, `TaskParams`, `ApprovalSignal`/`Record`, `GuardrailCheck`/`Outcome`, the reply/status shapes. |
| [`runtime_context.py`](runtime_context.py) | `RoleRuntime`: the process-level "this worker hosts exactly one role" (D16) context that workflow code reads (set once at startup, identical across replays). |
| [`codec.py`](codec.py) | `EncryptionCodec`: AES-256-GCM payload encryption (D9) so Temporal only ever stores ciphertext. |
| [`converter.py`](converter.py) | Wires the pydantic data converter together with the codec — and **fails closed** if `TEMPORAL_PAYLOAD_CODEC_KEY` is unset (D9). |
| [`schedules.py`](schedules.py) | Turns manifest `schedules` into Temporal Schedules that start `TaskWorkflow` on cron. Idempotent on every worker start. |

## Determinism notes

Workflow code must be replay-deterministic. Two consequences you'll see in the
code: (1) `with workflow.unsafe.imports_passed_through():` guards imports that
the sandbox would otherwise reject; (2) OTel trace context is captured per
queued turn and re-attached around processing (D19) — because the D13 queue
decouples update handling from turn processing, without this every turn's spans
would land in whichever trace was attached last. Behavior-changing edits to
this code ship behind `workflow.patched()` (D14; ledger in `PATCHES.md`).
