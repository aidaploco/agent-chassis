# `contracts/` — the vendor-free type vocabulary (D2)

**Read this folder first.** Every seam in the chassis speaks these types and
nothing else. They are the shared vocabulary that lets the workflow shell,
channels, guardrails, memory, and evals all stay ignorant of the agent SDK.

> **Hard rule:** nothing in this package may import a vendor runtime SDK
> (`pydantic_ai`). This is enforced by the import-linter contract in
> `pyproject.toml` — `contracts/` sits at the bottom of the dependency graph.

## The types

| File | Types | What it models |
|---|---|---|
| [`envelope.py`](envelope.py) | `MessageEnvelope`, `ContentPart` | The canonical inbound/outbound message. Channels normalize to/from this; the core never learns which channel it is on. |
| [`turn.py`](turn.py) | `TurnRequest`, `TurnResult`, `TurnDirective` (`Reply` / `Route` / `Escalate` / `NeedsApproval`), `TurnEvent`, `TurnContext`, `ApprovalItem`, `ApprovalDecision` | One agent turn: what the chassis asks for and what the adapter returns. The **directive** is how an agent tells the chassis what to do next (D4). |
| [`history.py`](history.py) | `ConversationHistory`, `HistoryBudget` | Conversation history — **opaque to the chassis** (D6a). Entries are vendor-shaped and owned by the adapter named in `runtime`; the chassis only carries them through workflow state and continue-as-new. |
| [`outcome.py`](outcome.py) | `OutcomeEvent`, `Resolution` | The **public metrics contract** (D12), one per completed conversation/task. Breaking changes require an `event_version` bump; a contract test pins the field set. |
| [`runtime.py`](runtime.py) | `RuntimeAdapter` (Protocol), `RuntimeHandle` | The adapter seam itself: the only three methods the chassis needs from an agent runtime — `build`, `run_turn`, `compact_history`. |

## Why history is `Any`

`ConversationHistory.entries` is typed `list[Any]` on purpose: the shape is the
adapter's, and the chassis must never depend on it. When a message-history
entry needs windowing, the chassis decides *when* (via `HistoryBudget`) but the
adapter decides *how* (`RuntimeAdapter.compact_history`). This is the one place
the D2 seam and the "history is workflow state" decision (D6a) meet.
