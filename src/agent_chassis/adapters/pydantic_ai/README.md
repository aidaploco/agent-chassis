# `adapters/pydantic_ai/` — the implemented runtime adapter (D1)

The `RuntimeAdapter` implementation on **pydantic-ai** wrapped by Temporal's
`TemporalAgent`. This is where an agent turn actually runs.

## Key pieces in [`adapter.py`](adapter.py)

- **`PydanticAIAdapter.build(resolved)`** — constructs one `TemporalAgent` per
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
  `TurnEvent`s (model calls, tool calls, tool outputs).
- **`compact_history(...)`** — the V1 windowing policy: keep the last
  `max_turns` user turns, fold the rest into a text summary marker.

## Why activity names are load-bearing

`TemporalAgent` derives activity names from **agent names**
(`{employee}_{agent}`) and **toolset ids** (capability instance ids). Those are
frozen once deployed (D1/D7) — which is why the manifest schema constrains
those identifiers so hard. Rename = new deployment.

## Two facts worth knowing

- **Structured outputs arrive as tool calls.** pydantic-ai delivers the
  `TurnDirective` union as calls to internal `final_result*` tools. The adapter
  filters those out (`_is_directive_call`) so they never pollute `tools_called`,
  the outcome counters, or eval tool grading.
- **The adapter runs fine outside Temporal.** `TemporalAgent` behaves like a
  plain agent when not inside a workflow — which is exactly why unit tests and
  the eval runner can drive it without a server.

The composition-root helpers `temporal_plugins()` and `bind_workflow_agents()`
let [`worker.py`](../../worker.py) register the vendor's plugins and activities
without importing the SDK itself.
