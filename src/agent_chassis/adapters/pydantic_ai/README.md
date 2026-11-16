# `adapters/pydantic_ai/` — the implemented runtime adapter (D1)

The `RuntimeAdapter` implementation on **pydantic-ai** wrapped by Temporal's
the Temporal `TemporalDurability` capability. This is where an agent turn
actually runs.

## Key pieces in [`adapter.py`](adapter.py)

- **`PydanticAIAdapter.build(resolved)`** — constructs one durable `Agent` (a
  regular pydantic-ai `Agent` carrying `capabilities=[TemporalDurability()]`) per
  manifest agent. It wires per-agent model/temperature/token overrides, builds
  a `FunctionToolset` from each capability pack's `ToolSpec`s, wraps
  approval-gated tools in an `ApprovalRequiredToolset` (D10), sets the allowed
  output types (`str` + `Route`?/`Escalate`/`DeferredToolRequests`?), and marks
  `pure=True` tools for in-workflow execution.
- **`run_turn(handle, request)`** — runs the agent for one turn and translates
  its output back into a chassis `TurnDirective`. A plain string → `Reply`; a
  `Route`/`Escalate` passes through; a `DeferredToolRequests` (a paused,
  approval-gated call) → `NeedsApproval`. It also serializes the vendor message
  history into `ConversationHistory.entries` and normalizes what happened into
  `TurnEvent`s (model calls, tool calls, tool outputs). If a model returns the
  same reply as two identical text parts, the duplicate is collapsed (a
  customer would otherwise read the sentence twice). The agent's instructions
  also carry the routing note and, after a compaction, a line telling it earlier
  turns are no longer visible.
- **`compact_history(...)`** — the V1 windowing policy: keep the last
  `max_turns` user turns (a routed turn, which repeats the user's message per
  hop, counts once), fold the rest into one running-total marker
  (`[N earlier turns compacted]`). The chassis triggers it once history holds
  1.25x `max_turns`. `summary_max_tokens` is accepted but unused until
  model-written summaries exist.

## Why activity names are load-bearing

`TemporalDurability` derives activity names from **agent names**
(`{employee}_{agent}`) and **toolset ids** (capability instance ids). Those are
frozen once deployed (D1/D7) — which is why the manifest schema constrains
those identifiers so hard. Rename = new deployment.

## Two facts worth knowing

- **Structured outputs arrive as tool calls.** pydantic-ai delivers the
  `TurnDirective` union as calls to internal `final_result*` tools. The adapter
  filters those out (`_is_directive_call`) so they never pollute `tools_called`,
  the outcome counters, or eval tool grading.
- **The adapter runs fine outside Temporal.** `TemporalDurability` is transparent like a
  plain agent when not inside a workflow — which is exactly why unit tests can
  drive it without a server. (The eval runner does not: it goes over HTTP
  against a running Employee.)

The composition-root helpers `temporal_plugins()` and `bind_workflow_agents()`
let [`worker.py`](../../worker.py) register the vendor's plugins and activities
without importing the SDK itself.
