# Agent Chassis — Design

This is the decision log for agent-chassis. Each numbered decision (D1–D24)
states what was decided, its consequences, and the alternatives that were
rejected and why. Decisions are accepted and implemented unless marked
**Proposed**; a superseded decision is struck through, never deleted. The
reference sections (manifest schema, seam contract, event contract) follow the
decisions, and the build history is at the end.

**Not implemented:** D24, and Mem-2/Mem-3 of D22, are proposals. D23 (Mem-1) is
built.

## What this is

A runtime chassis on which role-based AI Employees are defined, run, and
operated. An Employee is a **role manifest** (one YAML file) plus reused
**capability packs** — never a new codebase. The chassis provides durable
execution (Temporal), channels, guardrails, memory, observability, and
introspection; roles provide configuration.

Prototype: an earlier customer-service agent built on the openai-agents SDK. V1
must be able to express its billing/support/supervisor behavior as a manifest +
packs, proven by a ported eval suite.

Stack: Python 3.13, uv, FastAPI, **pydantic-ai** (runtime), **temporalio**
(durability), Postgres + pgvector (memory substrate), OpenTelemetry/Phoenix
(tracing), ruff, pytest + coverage gate, import-linter.

---

## Decisions

### D1 — Runtime: pydantic-ai wrapped by TemporalAgent

**Decision.** The implemented runtime is pydantic-ai. Durable execution of agent
turns uses `pydantic_ai.durable_exec.temporal.TemporalAgent`, which offloads
every model request and tool call to a Temporal activity automatically, with
per-tool opt-out (`tool_activity_config={toolset_id: {tool_name: False}}`) for
pure in-workflow tools. Worker wiring uses `PydanticAIPlugin` + `AgentPlugin`.

**Consequence (load-bearing).** Temporal derives activity names from agent names
and toolset ids. These are **frozen once deployed**. The manifest schema
enforces naming rules (D7) so stability is a validated property, not a
convention.

**Rejected: openai-agents SDK.** The prototype proves it works, but its
Temporal story is coarser (we ran whole turns as single activities) and its
vendor-native handoffs would couple routing to the SDK (see D4).
**Rejected: chassis-owned turn-as-single-activity (the prototype's
pattern).** Simpler, proven, but loses per-model-call/per-tool-call durability
and retry granularity, which this design requires.

### D2 — Runtime adapter seam: chassis owns the workflow shell

**Decision.** The chassis owns all deterministic orchestration: the message
loop, routing over the manifest graph, escalation states, timers, idle timeout,
continue-as-new. It depends only on chassis-owned types and a `RuntimeAdapter`
interface (see *Runtime seam contract* below). Only `adapters/pydantic_ai/`
imports `pydantic_ai`; enforced by an import-linter contract in CI. Chassis
signatures never mention vendor types.

**Honesty caveat, recorded.** `TemporalAgent` means vendor code decides activity
boundaries *inside* the workflow, so "workflows are vendor-free" is false at the
Temporal-topology level. The seam we guarantee is at the **code and type**
level: an openai-agents adapter could implement `RuntimeAdapter` (running the
turn as one activity behind the same interface) without touching chassis code.
That is the seam-honesty test and it is satisfiable by construction.

**Rejected: fully vendor-free workflows with turn-as-opaque-activity.** Would
make the seam trivially honest but forfeits the durability granularity D1 was
chosen for. **Rejected: no seam (vendor types throughout).** Fails the product
requirement outright.

### D3 — Messages arrive as workflow updates, not signals

**Decision.** A user/channel message enters a conversation via Temporal
**update-with-start** (`WorkflowIDConflictPolicy.USE_EXISTING`), the pattern
proven end-to-end in the prototype, including its
pod-kill-and-resume acceptance test. The update handler returns the turn result.
Sync channels (HTTP) await the update result. Async channels (Slack-shaped)
send the update without awaiting the outcome; the workflow delivers the reply
through an outbound-delivery activity on the channel adapter. One ingestion
mechanism, both channel styles.

**Escalation approvals are signals** — they are genuinely fire-and-forget: the
approver's decision (who/when/comment, D11) is delivered as a signal that a
waiting workflow consumes.

**Updates × continue-as-new (amended into the de-risk spikes).** The workflow
must drain before rolling over: the turn queue (D13) must be empty **and**
`await workflow.wait_condition(workflow.all_handlers_finished)` precedes every
`workflow.continue_as_new(...)`. Conversation state (D6a) is carried in the CAN
arguments. **A test must cover the rollover boundary**: messages sent
immediately before, across, and after a forced rollover are all answered, in
order, with history intact. To make the boundary testable, the CAN trigger is
injectable (`max_turns_per_run` in workflow params for tests) in addition to the
production trigger (`workflow.info().is_continue_as_new_suggested()`).

**Rejected: signals for messages.** No result path — the HTTP channel would need
a response side-channel (polling table or callback), reinventing what updates
already provide, and losing update dedup semantics.

### D4 — Routing is chassis-owned via structured turn directives

**Decision.** pydantic-ai has no handoff primitive (verified against source).
Instead of emulating one, routing becomes a chassis property: every agent turn
returns a `TurnDirective` —

```
reply(content)                 # respond to the user
route(target_agent, reason)    # pass the conversation to another capability agent
escalate(reason, payload)      # enter the human-escalation state (D10)
```

The **chassis** walks the manifest's `routing` graph deterministically: a
`route` directive is only followed if the edge exists in the manifest; hop count
per inbound message is bounded (default 3); an illegal or over-budget route is
an error surfaced to evals, never a silent fallback. Routing legality is thereby
a validated, auditable product property instead of implicit SDK wiring.

**Route-with-message semantics (explicit, amended).** A `route` directive
produces no user-visible text. The target agent receives the **same inbound
message** and the **full conversation history**; `reason` (and optional `note`)
are internal context injected into the target agent's instructions, never shown
to the user. The hop budget counts every followed edge for one inbound message;
if the budget is spent without a `reply`, the turn is an error surfaced to
evals and the user receives the chassis fallback message.

**Rejected: SDK-native handoffs.** Do not exist in pydantic-ai; would re-couple
the chassis to a vendor (contradicts D2). **Rejected: free-form routing (agent
names another agent in prose).** Unauditable, unbounded, untestable.
**Rejected: route-without-context (target sees only a routing note).** Breaks
conversational continuity — the prototype's invisible-handoff UX requires
the target to see everything the user said.

### D5 — Guardrails: input as chassis pre-flight activities, output as validators

**Decision.** Input guardrails (length, prompt-injection, off-topic — the
standard trio) run as **chassis activities before the agent turn**: they are
LLM calls needing durability and retry, they are parameterized per manifest
(thresholds within schema-validated bounds), and they stay vendor-free so a
guardrail pack survives a runtime swap. Output-side checks use pydantic-ai
`output_validator` inside the adapter but are declared and configured through
the same chassis guardrail interface, so the manifest sees one guardrail model.
Every threshold override in a manifest is validated against the pack's declared
bounds and written to the audit log (D11).

**Rejected: vendor validators only.** Input-side checks would live inside the
adapter, become unswappable, and lose independent retry policy.

### D6 — Two workflow types, one activity substrate

**Decision.** `ConversationWorkflow` (long-lived; update-driven; idle timeout;
CAN) and `TaskWorkflow` (Temporal-Schedule-triggered or one-shot;
run-to-completion; may durably wait on approval signals for days). Both use the
same activities, adapter, guardrails, memory, and outcome-event emission. The
V1 second role (ops/reporting) runs on `TaskWorkflow`; Lana runs on
`ConversationWorkflow`.

### D6a — Conversation state lives in workflow state, not an external session store

**Decision.** The pydantic-ai message history for a conversation is workflow
state, carried through continue-as-new. Temporal history is already durable and
payload-encrypted (D9); no Redis-style session store is added.

**History compaction is a seam method (amended — resolves the D6a/D2 tension).**
History entries are vendor-shaped, so the chassis cannot window them itself.
The chassis decides *when* (policy: the manifest's `runtime.history_budget`);
the adapter implements *how*: `RuntimeAdapter.compact_history(handle, history,
budget)` runs as an **activity** (it may call a model to summarize) and returns
a history within budget. Compaction runs between turns — the turn queue (D13)
guarantees no concurrent turn — and its result deterministically replaces
workflow state. Notable events and decisions are additionally persisted to the
memory substrate via write-back (D8), which is the long-term record.

**Rejected: external session store (the prototype used Redis).** Extra
infrastructure, a second source of truth, and the exact class of read/write
races the workflow's serialized turns eliminate.

### D7 — Role manifest schema v1: versioned, fail-closed, naming-stable

**Decision.** Manifests are validated by a versioned pydantic schema
(`schema_version: 1`, `extra="forbid"` everywhere — unknown fields are errors).
Identifier rules enforced by the schema, because Temporal activity names derive
from them (D1): `employee.name`, agent names, and toolset/pack instance ids are
`^[a-z][a-z0-9_]{2,40}$` and are **immutable per deployment**; renames are new
deployments. Guardrail threshold overrides must fall inside the pack's declared
bounds or validation fails. The full schema with an example manifest is in
*Manifest schema v1* below.

### D8 — Memory substrate: retrieval + write-back, one interface

**Decision.** `MemorySubstrate` has two halves: `retrieve` (semantic + keyword
over the employee's knowledge and history) and `record` (structured write-back:
decisions, outcomes, notable events — employees write, not only read).
One implementation over Postgres + pgvector in V1. The interface docstring
names the intended future: a shared, MCP-based knowledge substrate plugs in here.
Memory operations are activities (I/O) and are traced (D12).

### D9 — Payload encryption fail-closed from day one

**Decision.** The AES-256-GCM `PayloadCodec` pattern from the prototype
is ported (pydantic-ai's Temporal integration preserves a custom
`payload_codec` on its data converter — verified against source). The worker
**fails closed**: no `TEMPORAL_PAYLOAD_CODEC_KEY` ⇒ refuse to start. Dev
convenience is an explicitly-set local key in docker-compose, never an implicit
plaintext mode. Key format: base64-encoded 32 bytes; `encryption-key-id`
metadata for rotation.

### D10 — Human escalation is a first-class workflow state

**Decision.** `escalate` (from a turn directive, a guardrail, or an
approval-required tool) moves the workflow into `WAITING_ON_HUMAN`: it emits an
escalation record, then durably waits on an approval **signal** with
timer-driven reminders and an expiry path (remind after R, expire after E, both
manifest-configurable). pydantic-ai's `approval_required` toolset is the
adapter-level mechanism for approval-gated tools; the chassis state machine is
the same regardless of what triggered the wait. "Ask a human and wait two days"
is thereby a normal, durable thing.

### D11 — One audit log for manifest overrides and approval decisions

**Decision (per amendment).** A single append-only audit stream records both
(a) every manifest guardrail/threshold override (role, field, value, bounds,
actor, timestamp) and (b) every approval decision (workflow id, decision,
**who / when / comment**). Same schema family, same store (Postgres table),
same retention policy. Approval signals carry the audit fields; the workflow
writes the audit entry via activity before acting on the decision.

### D12 — Outcome events are a public contract

**Decision.** Every completed task/conversation emits one structured
`OutcomeEvent` (schema below). It is versioned (`event_version`), has its own
contract tests (golden serialization), and is emitted as a Postgres row + OTel
span event + structured log line. This is the raw feed for
usage, cost and reliability metrics. Breaking changes require a
version bump and a migration note in this file.

### D13 — Inbound turns are serialized through an in-workflow turn queue

**Decision (per amendment).** Concurrent updates on one conversation never
interleave agent runs. Each accepted update enqueues onto an in-workflow FIFO
turn queue; a single consumer processes one turn at a time. Update handlers
still await their own turn's completion and return its result (D3) — the queue
is a coordination structure with observable depth and guaranteed arrival-order
processing, not a fire-and-forget buffer. Before continue-as-new: queue empty
**and** all handlers finished. **Spike + permanent regression test**: N updates
delivered concurrently (same workflow task where possible) are all answered, in
arrival order, none lost, including across a forced CAN rollover.

**Rejected: `asyncio.Lock` only (the prototype's pattern).** Serializes but
implicitly — no observable queue depth, ordering an artifact of lock fairness
rather than a stated guarantee, nothing to drain deterministically before CAN.

### D14 — Workflow code versioning: `workflow.patched()` discipline

**Decision (per amendment).** Conversations live hours-to-days; deploying
changed workflow code mid-conversation is a certainty. V1 uses **`patched()`
discipline**: every behavior-affecting change to workflow-deterministic code
ships behind `workflow.patched("<change-slug>")`; removal follows Temporal's
two-step (`patched` → `deprecate_patch` → delete) only after old runs drain —
bounded by `idle_timeout + max conversation length`, since conversations
cannot outlive their idle timeout. CI reminder: a `PATCHES.md` ledger lists
active patches and their earliest-removal dates. **Spike (mid-conversation
deploy)**: start a conversation on revision A, redeploy with a `patched()`
change, continue the same conversation — replay must succeed and new
conversations must take the new path.

**Rejected: worker build-id versioning.** Cleaner in theory, but requires the
deployment to operate parallel worker fleets with queue pinning/ramping — an
infrastructure capability we neither control nor may couple to. Revisit when
the deployment environment exposes build-id routing.

### D15 — Outbound delivery is idempotent per structured delivery key

**Decision (per amendment, key generalized).** Outbound-delivery activities are
at-least-once; channel adapters MUST suppress duplicates. Every outbound
delivery carries an idempotency key
`{conversation_id}:{delivery_kind}:{sequence}`:

- conversation reply → `…:turn:{turn_number}`
- escalation reminder → `…:reminder:{escalation_id}:{k}`
- scheduled/proactive message → `…:task:{task_run_id}:{n}`

The fake channel adapter's test suite includes duplicate-delivery suppression
(same key delivered twice → exactly one send). The HTTP sync channel is
unaffected (replies return via the update result, no outbound activity).

**Rejected: `conversation_id + turn_number` alone.** Collides with
non-turn deliveries (reminders, scheduled messages) that have no turn number.

### D16 — Tenancy: single-tenant per deployment

**Decision (per amendment).** The runtime is deployed as one agent-unit per
tenant. Tenant **isolation** (namespace, database, secrets,
network) is the deployment environment's responsibility, not the chassis's. The substrate
DSN is assumed tenant-scoped. The `tenant` field on envelopes and outcome
events is a passthrough correlation label, never an isolation mechanism —
no chassis code branches on it.

**Rejected: in-process multi-tenancy.** Duplicates guarantees the deployment
environment already provides and expands the blast radius of every chassis bug to all
tenants.

### D17 — Eval contract

**Decision (per amendment; lessons from an earlier eval review baked
in).** Evals are chassis machinery with a versioned case schema, not per-app
scripts.

- **Case schema v1 (YAML):** `id`, `messages` (multi-turn supported from day
  one — assistant turns allowed), `expected_agent`, `expected_tools`,
  `expected_directive` (reply/route/escalate), `guardrail_expectations`,
  `quality_prompt`.
- **Judge (amended 2026-07-10):** LLM-as-judge receives the **full
  conversation** (not just the last exchange) plus the case's criteria and
  returns a **binary pass/fail with mandatory reasoning stated before the
  verdict**; explicit failure conditions (criteria unmet, invented facts,
  instruction violations, repetition/evasion) are listed in the judge prompt.
  Judge model is env-configured.
- **Rejected: numeric 1-5 scale with pass threshold ≥ 4** (the original D17
  design, live through 2026-07-10). LLMs are poorly calibrated on numeric
  scales and regress to the middle of the range, which made the 4-vs-3
  boundary carry all the signal on the mushiest part of the scale. A binary
  verdict against explicit failure criteria is what the threshold was
  approximating anyway, and the reasoning field keeps failures auditable.
  (The prototype's ≥3 "acceptable but missing something important" bar
  remains rejected for the same reason, harder.)
- **Ownership:** every capability pack bundles its own suite (regression tests
  travel with the capability); a role's suite = union of its packs'
  suites + role-level cases. CI gates on per-role pass rate.

**Rejected: bespoke per-app harness (prototype pattern).** Single-turn
blind, non-reusable, judge under-informed — the exact gaps found in the
2026-07-08 review.

### D18 — Deployment model: two units, one image (2026-07-09)

**Decision.** A role deploys as two units built from one image — an HTTP
channel unit and a Temporal worker unit — selected by a command override,
sharing one task queue, with `MANIFEST_PATH` pointing at the role's YAML.
Nothing role-specific is baked into the image; the deployment layer never
needs to know what a "role" or "capability pack" is.

**Confirmed: the fail-closed codec (D9) needs no deployment-side support.**
`TEMPORAL_PAYLOAD_CODEC_KEY` is delivered as a secret-backed env var and the
worker refuses to start without it.

**Named, not solved — production parity for memory depends on infrastructure
outside this repo.** Postgres/pgvector needs a database the deployment
provides. This does **not** block *building* memory (D8's Postgres+pgvector
runs against a local/dev Postgres) and tracing is optional and no-ops
without a configured collector. It blocks **running a real tenant's
Employee with working memory** until that infrastructure exists. This is an
external dependency to track, not a chassis defect — recorded here so it
surfaces early.

**Observability delivery.** Tracing is configured by env var only:
`PHOENIX_COLLECTOR_ENDPOINT` unset = tracing disabled, set = export, never
fail hard (`observability.py`). If a deployment later supplies an ingest
key, the chassis sends it as an OTLP header through the standard
`OTEL_EXPORTER_OTLP_HEADERS` mechanism — nothing is built until then. The
D19 trace shape was checked manually against a live Phoenix: the complete
chain landed in the role's own trace project — all three guardrail spans as
children of `conversation.turn` in both turn traces, `session.id` grouping
two trace ids into one session, and zero interceptor noise verified under a
genuine Temporal replay: the two turns straddled a worker kill with 0
duplicate span ids and no re-emitted turn-1 spans (D14's replay discipline
and D19's trace contract verified together).

**Named, not solved — secrets delivery is in tension with the chassis's own
pitch.** "New Employee = new YAML, never new code" is undercut if onboarding
still requires a human to create secrets by hand against the live
environment. No design is proposed here, but leaving it unstated would be
false advertising. Revisit trigger: the first time onboarding a real
third-party Employee is attempted end-to-end.

**Verification (2026-07-10), manual.** The compose pod-kill test passed
locally, and the same property was exercised on a Kubernetes cluster (same
workflow run ID, context recalled, zero external store — durability from
Temporal workflow state alone, D6a vindicated). The Kubernetes run is not
reproducible from this repository, which ships no cluster manifests.

**Amended: M3.5 gains a hard second gate.** Passing on docker-compose proves
the chassis is durable; it does not prove it is durable under a real
orchestrator, which adds admission policy, per-workload identity, and config
delivery that compose lacks. M3.5 is not complete until the walking
skeleton **also** passes a pod-kill-and-resume demo against a real unit on a
Kubernetes cluster. This is accepted as a real dependency of the milestone,
not deferred to "later."

### D19 — Trace semantics: one readable tree per turn (2026-07-11)

**Decision.** Traces are a product surface, not plumbing exhaust. The
contract, verified attribute-by-attribute against a live Phoenix:

- **Per-turn traces, per-conversation sessions.** Each turn is one trace
  rooted at the channel's `conversation.turn` span (kind CHAIN, with
  `input.value`/`output.value`, agent, turn number); `session.id` =
  conversation id on every chassis span groups turns in Phoenix's Sessions
  view. Because the D13 queue decouples update handling from turn
  processing, the workflow captures the caller's OTel context per queued
  turn and re-attaches it around processing — otherwise every turn's spans
  land in whichever trace was attached last (observed live).
- **Temporal is invisible in traces.** `OpenTelemetryInterceptor`
  (`add_temporal_spans=False`) propagates context through
  workflow/activity headers without emitting spans. It requires the global
  provider to be Temporal's replay-safe one, so `setup_tracing` always
  installs `create_tracer_provider(...)` (exporter attached only when the
  collector endpoint is set).
- **Guardrails are first-class spans.** The `run_guardrail` activity wraps
  each check in a span with openinference kind GUARDRAIL, the message as
  input and pass/trip+reason as output. Pre-D19 they were invisible.
- **Agent/LLM/tool spans stay pydantic-ai's own.** Its GenAI-semconv spans
  (`invoke_agent`/`chat`/`execute_tool`, full input/output messages at
  instrumentation version 5) map natively to Phoenix's agent/llm/tool kinds.

**Rejected: legacy `TracingInterceptor`.** Emitted `StartActivity:`/
`RunActivity:`/`HandleUpdate:` span pairs — twelve kind-`unknown` plumbing
spans per turn, outnumbering the real spans (measured: 6 of the top 8 span
types in the Lana project were interceptor noise).

**Rejected: `openinference-instrumentation-pydantic-ai` + global OpenAI
instrumentor.** Unnecessary (Phoenix already maps pydantic-ai's spans) and
harmful respectively — instrumenting the OpenAI client globally would nest a
duplicate LLM span under every pydantic-ai `chat` span. Packs that call
providers directly (Lana's LLM guardrails) own their own instrumentation;
the chassis guardrail span records verdict and input regardless.

### D20 — Where role artifacts live: split by trust tier, not maturity (2026-07-11)

**Decision.** The image is chassis-only, and role artifacts reach it by one
of two permanent mechanisms, chosen by *who wrote the code*, not by how
mature the delivery pipeline is:

- **This repo holds:** the chassis, builtin packs (memory, introspection),
  and `examples/` as permanent test fixtures — Lana is the D17 acceptance
  instrument and stays even if a production Lana is ever promoted out.
- **Curated first-party packs → baked into the image, shipped by version
  bump.** Needs no new deployment capability. This repo's CI — tests, ruff,
  import-linter — *is* the code-review/supply-chain gate the moment pack
  source lives where CI touches it.
- **Tenant-custom packs → mounted at deploy time (`PACKS_DIR`),
  permanently.** Not a stopgap: baking one tenant's code into a shared
  chassis image co-mingles it into an artifact every other tenant's rollout
  depends on. Named plainly: `PACKS_DIR` code runs with full worker-process
  privileges (same env, same secrets) — the chassis does not sandbox it.
  That is acceptable *only* because the deployment's isolation unit is
  per-tenant; the trust boundary is the deployment's, not the chassis's.
- **Manifests are deployment configuration**, mounted as files
  (`MANIFEST_PATH`), not baked into the image. How they are stored and
  reviewed is up to whoever runs the chassis.
- **Manifests are not pre-cleared "just data".** They set guardrail
  thresholds and `approval_required_tools` — business-logic risk. Whatever
  review gate eventually exists must cover manifests and packs alike.

**Rejected: the maturity framing** (mounted delivery as bootstrap, bake
everything later) — "bake everything" fails multi-tenancy for tenant-authored
code. **Rejected: per-tenant images** — a commitment far beyond anything
needed.

*Cheap follow-up hook (not built):* the registry already knows each pack's
provenance (builtin / entry-point / `PACKS_DIR`); surfacing that trust tier
in `/v1/self` and the audit stream would make the boundary observable.

### D21 — Channel authentication: audited identity is never caller-supplied (2026-07-12)

**Decision.** Until D21, `/v1/approvals` trusted a request-body `approver`
string straight into the D11 audit log — anyone with network reach could
approve gated tools under any name; network policy was the only
control. Now:

- **Bearer tokens via env** (`HTTP_BEARER_TOKENS="token:principal,…"`),
  delivered through the same secret mechanism as `OPENAI_API_KEY` and the
  codec key — zero deployment-side change. When configured, every `/v1/*` endpoint
  (not `/healthz`) requires a valid token.
- **Split posture when unconfigured:** `/v1/tasks` and `/v1/approvals`
  refuse to serve (503) — an unauthenticated approval is an unattributable
  audit entry, which defeats D11's purpose. `/v1/messages` and `/v1/self`
  serve anonymously for local dev, with the posture logged loudly at
  startup. Fail-closed where identity matters, fail-noisy where it doesn't.
- **The audited approver IS the authenticated principal.** The `approver`
  request field is gone; the token→principal map is the identity source.
  Real SSO/OIDC arrives with an ingress layer and replaces the
  token map, not this contract.
- **Slack inbound** verifies the signing-secret HMAC (v0 scheme, 5-minute
  replay window) when `SLACK_SIGNING_SECRET` is set; unsigned webhooks are
  a logged local-dev posture.

**Rejected: keeping body-supplied approver alongside auth** (redundant and
forgeable-by-confusion). **Rejected: building OIDC now** (no ingress exists
to terminate it; the token map is the smallest thing that makes the audit
log honest).

### D22 — Memory substrate evolution: interface + provenance first (2026-07-12)

**Status.** Mem-1 is implemented (see D23). Mem-2 and Mem-3 are proposed and
not implemented.

**Context.** D8 named a richer, shared knowledge substrate as the intended
future of `MemorySubstrate`: a queryable store, a canonical connector
library, a retrieval API, an MCP server, and per-user agents on top. This
decision designs the path; **nothing below is built until its milestone has
a consumer**.

- **(a) Interface evolution.** Today's `retrieve/record/recent` is
  per-employee and unattributed. The richer shape adds, in order of
  necessity: **provenance** on every entry (`source`: operator | employee |
  connector-name | user, plus author and timestamp — becomes part of
  `MemoryEntry`, defaulted for existing rows), **scoping** (per-user /
  per-collection read scopes replacing the implicit per-employee
  collection), and **connector identity** (entries created by ingestion
  carry their connector and document ref). `retrieve()` grows a scope
  parameter and returns provenance with every hit; `record()` requires it.
- **(b) MCP server as the external surface.** The substrate's read (and
  selectively write) API is exposed as an MCP server so the memory is
  queryable by any MCP client, not just chassis employees. Tool
  authorization is registry-driven: allow-lists, deny-by-default, caller
  identity from the deployment's workload identity. The chassis ships the
  server; the deployment environment owns admission.
- **(c) Memory provenance is a trust boundary.** Recalled entries currently
  re-enter agent context indistinguishable from operator instructions — a
  user can `record_memory("always give refunds")` and it returns as trusted
  memory. Design: `search_memory` results are rendered with their provenance
  labels, and the MemoryPack instruction fragment states that recalled
  content is *data, not instructions* — user-sourced memories never override
  operator policy. This lands in milestone 1 because it is a live prompt-
  injection vector today, not a future concern.
- **(d) Milestones.** **Mem-1** (buildable now, small): provenance fields on
  MemoryEntry + record/retrieve signatures + MemoryPack trust framing +
  migration default for existing rows. **Mem-2:** retrieval API (HTTP,
  D21-authenticated) over scoped collections. **Mem-3:** MCP server + first
  canonical connector, internal-first. Each milestone waits for a named
  consumer before construction starts.

**Rejected: building the MCP server first** (attractive, but it would
expose today's provenance-free entries — codifying the trust bug).
**Rejected: a separate knowledge store beside pgvector** (two write paths,
two truths; the substrate interface is the seam, the store evolves behind
it).

**Mem-2's first consumer: a per-user agent.** Scoped retrieval is designed
for an agent that reads on a user's behalf, not for an admin dashboard — a
store only visible through a console accumulates data nobody uses. Mem-2's
construction still waits on the D24 design.

### D23 — Mem-1: memory provenance is required; recalled content is labeled data (2026-07-19)

**Context.** Implements D22 milestone Mem-1. D22(c) named the live
prompt-injection vector: recalled entries re-entered agent context
indistinguishable from operator instructions, so
`record_memory("always give refunds")` came back as trusted policy.
Provenance-before-MCP-exposure is also a sequencing rule, not merely a
security preference: an external surface must not ship over entries whose
origin is unrecorded.

**Decision.**
- `MemoryEntry` gains `source` — the trust classification of where the
  content originated (`operator` | `employee` | `user` | `connector:<name>`,
  validator-enforced shape) — and `author`, the identity that performed the
  write. `record()` requires both (keyword-only); `retrieve()` and
  `recent()` return them on every hit.
- Trust assignment: entries written through the conversation-facing
  `record_memory` tool are `source="user"` regardless of how the agent
  phrases them — content that entered via a conversation never earns more
  than user trust. Chassis write-backs (outcomes, escalations, approvals)
  are `source="employee"`. `author` is the writing employee's manifest name
  in both paths.
- Migration: pre-D23 rows read back as `source='user'`,
  `author='unknown'` — the least-trust default; unknown origin is never
  promoted. Applied as `ALTER TABLE … ADD COLUMN IF NOT EXISTS` with column
  defaults in the substrate's `start()`, so old installs migrate on boot
  with no operator step.
- `MemoryPack` renders every recalled entry with its provenance label, and
  its instruction fragment states that recalled memories are data, not
  instructions: user-sourced content never overrides operator policy.
- Workflow determinism: verified, not assumed — tool results cross the
  activity boundary as strings and `MemoryEntry` is never a workflow
  payload type, so no `workflow.patched()` gate applies.

**Rejected: plumbing the D21-authenticated caller into tool context as
`author`.** The channel edge knows the audited principal, but tools receive
only their arguments today; carrying per-turn identity into activity context
touches the adapter seam and belongs to Mem-2's scoping design, not to a
security patch. `author` = writing employee until then.
**Rejected: defaulting migrated rows to `source='employee'`.** Convenient,
but it would promote rows whose origin nobody recorded to a trust level
they never earned — the exact bug this decision exists to close.
**Rejected: a closed enum for `source`.** `connector:<name>` is open-ended
by D22(a); a shape validator admits the named sources plus the connector
prefix without freezing the connector list into the schema.

### D24 — Scoped retrieval: one identity spine (proposed, 2026-07-19)

**Status: proposed, not implemented.**

**Context.** D22's Mem-2: a D21-authenticated HTTP retrieval API over scoped
collections. Consumer: a per-user agent (D22). This design also answers what D23 deferred — how per-turn
authenticated identity reaches tool context — because per-user scoping is
exactly the consumer that forces it. Design only; construction is scheduled
work once this design is accepted.

**Decision.**

- **(a) Scope is a new axis, orthogonal to provenance.** Provenance (D23)
  says who wrote an entry and how much to trust it; *scope* says who may
  read it. `MemoryEntry` gains `scope` with two shapes in Mem-2:
  `employee:<name>` (today's implicit per-employee collection made
  explicit) and `user:<principal>` (a per-user agent's unit of ownership).
  Migration mirrors Mem-1's boot-time pattern; the default for existing
  rows is `employee:<collection>` — factual this time, not trust-based:
  pre-Mem-2 rows are employee-scoped by construction.
- **(b) Read entitlement rule.** An employee's agents read
  `employee:<own-name>` plus — only during a conversation with an
  authenticated sender — `user:<sender>`. An API principal `p` reads
  `user:p` and nothing else by default; additional grants are explicit
  deployment config (`MEMORY_SCOPE_GRANTS="principal=scope,…"`), a
  deliberate placeholder with the same trajectory as D21's token map:
  OIDC claims replace both, not this contract. `retrieve()` grows a
  `scopes` parameter; the substrate enforces scope in the query, the
  caller never filters post-hoc.
- **(c) Write rule.** `record_memory` during an authenticated user
  conversation writes `scope=user:<sender>` — a user's memory accumulates
  through their conversations with employees. Without an authenticated
  sender it stays `employee:<name>` (dev posture). Chassis write-backs
  (outcomes, escalations, approvals) remain `employee:<name>` — they are
  the employee's operational record, not the user's knowledge.
- **(d) HTTP surface.** `POST /v1/memory/search`
  (`{query, limit?, kinds?, scopes?}` → hits with content, provenance,
  scope, score). D21 fail-closed posture, same class as `/v1/approvals`:
  when `HTTP_BEARER_TOKENS` is unset the endpoint refuses to serve —
  an unauthenticated read of user-scoped memory is a data leak, not a dev
  convenience. Requested scopes must be a subset of entitled scopes; the
  400 names the offending scope, never returns partial results silently.
  Read-only: D22(b)'s "selectively write" surface is Mem-3 design work.
  The route lives on the existing API unit — no new service and no new
  egress — and is in-cluster only by design.
- **(e) Identity spine: envelope → deps → contextvar → tool.** The audited
  sender already reaches the adapter (`TurnRequest.message.sender` — D21
  guarantees it is never caller-supplied). The adapter threads a
  chassis-owned `TurnIdentity` (sender, channel, tenant) through
  pydantic-ai's serializable agent deps so it rides into tool activities;
  the adapter-owned tool wrapper (the same layer that builds tool
  activities today) stashes it in a chassis contextvar before invoking the
  vendor-free handler; packs read `get_turn_identity()` from the runtime
  context. No workflow payload changes, no vendor type crosses the pack
  seam (D2's import-linter contract stays green by construction). This
  also upgrades D23's deferred `author`: `record_memory` entries carry the
  authenticated sender as author once the spine exists.

**Rejected: per-user tables or schemas.** Two write paths, two truths —
re-rejected for the same reason D22 rejected a parallel store; scope is a
column and a query predicate. Tenant isolation stays the deployment's job
(D16); scope is intra-tenant read control, not a security boundary against
a hostile operator of the deployment.
**Rejected: pydantic-ai `RunContext`-typed tool signatures in packs.**
It would put a vendor type in every pack — the exact D2 violation the
import-linter contract exists to catch. The adapter wrapper + contextvar
buys the same capability seam-clean.
**Rejected: scope grants as a third field in `HTTP_BEARER_TOKENS`.**
The token map stays single-purpose (authentication); entitlement lives in
its own variable. Both are OIDC placeholders; overloading the CSV would
couple their replacement schedules.
**Rejected: serving `/v1/memory/search` anonymously when auth is
unconfigured** (the `/v1/messages` posture). New surface, no compat debt,
and user-scoped data behind it — it takes the `/v1/approvals` posture
instead.

**Open questions.**

**(1) `MEMORY_SCOPE_GRANTS` delivery.** It is authorization policy (who
may read what), not a credential. Hiding policy in the secret store would
undermine the same property D21 protects for `/v1/approvals`: an audited
decision must trace to something reviewable, never an opaque value. It is
therefore delivered as ordinary, reviewable deployment configuration
(a list of `"principal=scope"` strings rendered into the
`MEMORY_SCOPE_GRANTS` env var), not through the secret mechanism. The OIDC
trajectory is the same as the token map's: entitlement claims eventually
replace this variable. It is not built yet: nothing needs non-default
entitlement. **This is not on Mem-2's construction critical path**: D24(b)'s
default entitlement (own employee scope + own authenticated user scope,
deny else) requires no grants config at all — the acceptance demo below
exercises only default entitlement and its denial, never the grants
mechanism.

**(2) Acceptance demo** — value shown flowing
through an agent-shaped read path, not an admin console:
1. Two authenticated principals in `HTTP_BEARER_TOKENS` (e.g. `alice`,
   `bob`), no `MEMORY_SCOPE_GRANTS` configured.
2. A real Lana conversation as `alice` records at least one entry via
   `record_memory`; it lands `scope=user:alice` (D24c).
3. `POST /v1/memory/search` as `alice` with no `scopes` (defaults to
   entitled scopes) returns that entry with provenance and scope populated.
4. The same request with `scopes=["user:bob"]` gets 400 naming the
   offending scope — not a silent empty result (D24d).
5. The same request with no bearer token gets refused, matching the
   `/v1/approvals` fail-closed posture (D21).

---

## Manifest schema v1 (shape)

```yaml
schema_version: 1
employee:
  name: lana                 # ^[a-z][a-z0-9_]{2,40}$ — frozen per deployment
  role: "Customer service for Brightloop"
  persona:
    display_name: "Zara"
    tone: "direct, warm, leads with the answer"
runtime:
  model_tier: standard        # tier → concrete model mapping is env config
  temperature: 0.3
  max_output_tokens: 1024
  per_agent: {}               # optional per-agent overrides, same fields
  history_budget:
    max_turns: 40             # compaction trigger (D6a)
    summary_max_tokens: 512   # budget for the rolling summary
capabilities:
  - pack: billing             # registry id
    id: billing               # instance id (frozen; default = pack name)
    config: {...}             # validated by the pack's pydantic config model
  - pack: support
    config: {...}
routing:
  entry: supervisor           # agent that receives new conversations
  agents:
    supervisor: {capabilities: []}          # routing-only agent
    billing:    {capabilities: [billing]}
    support:    {capabilities: [support]}
  edges:                      # legal route directives; anything else is an error
    - [supervisor, billing]
    - [supervisor, support]
    - [billing, supervisor]
    - [support, supervisor]
  max_hops_per_message: 3
channels: [http]              # subset of registered adapters
guardrails:
  - pack: message_length
    config: {max_chars: 1000}       # must satisfy the pack's declared bounds
  - pack: prompt_injection
    config: {threshold: 0.8}
  - pack: off_topic
    config: {threshold: 0.7}
memory:
  substrate: pgvector
  collection: lana            # namespace within the substrate
escalation:
  approvers_channel: http      # where escalation requests surface in V1
  remind_after: PT4H
  expire_after: P2D
  approval_required_tools: []  # toolset/tool ids gated on human approval
kpis:
  task_types: [conversation]   # outcome-event task_type values this role emits
schedules: []                  # TaskWorkflow triggers (cron + task name); ops role uses this
```

Fail-closed loader: unknown field anywhere ⇒ validation error naming the path.
Pack `config` blocks are validated by each pack's own pydantic model, fetched
from the registry at load time.

## Runtime seam contract (chassis-owned types only)

```python
class RuntimeAdapter(Protocol):
    def build(self, manifest: RoleManifest, packs: ResolvedPacks) -> RuntimeHandle:
        """Construct runtime agents; called once per worker process."""

    async def run_turn(self, handle: RuntimeHandle, request: TurnRequest) -> TurnResult:
        """Execute one agent turn. Inside a workflow this may expand to
        multiple activities (vendor-managed); the chassis does not care."""

    async def compact_history(
        self, handle: RuntimeHandle, history: ConversationHistory, budget: HistoryBudget
    ) -> ConversationHistory:
        """Reduce history to within budget (may summarize via a model call).
        Invoked by chassis policy as an activity, between turns (D6a)."""

@dataclass
class TurnRequest:  # chassis-owned
    agent: str                       # manifest agent name
    message: MessageEnvelope | None  # None for scheduled/task turns
    history: ConversationHistory     # opaque-to-chassis, serializable
    context: TurnContext             # tenant, conversation id, run ids

@dataclass
class TurnResult:
    directive: TurnDirective         # reply | route | escalate
    history: ConversationHistory     # updated
    events: list[TurnEvent]          # normalized tool calls, usage, timings
```

`MessageEnvelope`: `sender`, `tenant`, `conversation_id`, `content_parts`,
`channel`, `metadata`. Channel adapters normalize to/from it; the core never
learns which channel it is on. Outbound delivery is idempotent per structured
delivery key — see D15.

## Outcome event contract v1

```
event_version: 1
role: str            tenant: str          task_type: str
conversation_id: str workflow_run_id: str
resolution: resolved | escalated | abandoned | expired
turns: int           tool_calls: int
wall_time_ms: int    tokens_in: int       tokens_out: int
escalations: int     guardrail_trips: int
models_used: list[str]   # distinct model identifiers observed in turn events
started_at / ended_at: datetime (UTC)
```

## Quality gates

Typed throughout; ruff (format + lint) clean; pytest with coverage gate (≥80%); Temporal tests on the time-skipping environment
where possible, real dev server where updates/CAN semantics demand it;
import-linter contract in CI; eval gate in CI; conventional commits.

## Configuration contract

Everything a deployment supplies reaches the chassis as environment variables,
declared in one place: `ChassisSettings` (`config.py`). `MANIFEST_PATH` and
`PACKS_DIR` point at the role's artifacts; where those are mounted from is up
to whoever runs the chassis.

---

## Project history

How the design was built and verified, in order. Kept for context; the
decisions above are the source of truth.

### De-risk spikes (before the workflow shell is finalized)

1. Multiple `TemporalAgent.run()` calls in one workflow run; message-history
   serialization shape.
2. History round-trip across continue-as-new (D6a) — including size behavior.
3. **Updates × CAN rollover boundary (amended)**: handler drain before CAN;
   messages sent before/across/after a forced rollover all answered in order.
   This is both a spike and a permanent regression test.
4. Per-tool activity opt-out (`False` config) with async pure tools.
5. `approval_required` toolset behavior under Temporal (deferred approval flow).
6. **Concurrent updates (amended, D13)**: N updates delivered concurrently are
   answered in arrival order, none lost, including across a forced CAN
   rollover. Spike and permanent regression test.
7. **Mid-conversation deploy (amended, D14)**: conversation started on
   revision A continues correctly after deploying revision B with a
   `patched()` change; new conversations take the new path.

### Milestones

- **M0** this document reviewed and committed.
- **M1** scaffold + contract kernel + manifest loader (Phases 1–2).
- **M2** packs + adapter + import-linter green (Phases 3–4).
- **M3** durable chassis + channels (Phases 5–6).
- **M3.5 (amended twice) — walking skeleton**: a trivial single-agent role (one
  echo-like capability, no routing) exercising **channel → workflow → adapter →
  outcome event → trace** end-to-end under docker-compose, **including
  pod-kill-and-resume**, before any Lana work. **Second gate (D18):** the same
  property re-verified on a real Kubernetes cluster. Gate: no Lana code lands until
  *both* pass.
- **M4** memory + observability + introspection (Phases 7–8).
- **M5** Lana parity via ported eval suite (Phase 9).
- **M6** second role + full V1 acceptance matrix (Phase 10).

**M6 CLOSED 2026-07-11.** TaskWorkflow (D6) with the D10 approval state
machine (pause on gated tools via the adapter's deferred-tool path, approve
by signal, timer reminders, expiry auto-denies as approver "system") and D11
approval audit records; manifest gained a `tasks:` section that `schedules:`
references (cross-validated, fail-closed); the worker ensures Temporal
Schedules from the manifest on start; HTTP grew `/v1/tasks` +
`/v1/approvals`. Second role: **Nova** (ops/reporting, `examples/nova/`) —
single reporter agent, `publish_report` approval-gated, daily cron. The
acceptance matrix passed: same builtin guardrail packs at different
thresholds/topics across both roles (overrides audited; the guardrail trio
was promoted to a chassis builtin, with the off_topic topic made manifest
config precisely because a builtin must not hardcode one employee's
business); outcome events for both role shapes; zero chassis special-casing
now *mechanically enforced* by a test scanning chassis source for role
names (it caught a real offender: the eval CLI defaulted to Lana's suite);
live pod-kill mid-approval on compose — the WAITING_ON_HUMAN pause survived
a worker kill and the post-restart approval completed the task. Lana suite
12/12 after the guardrail promotion.
