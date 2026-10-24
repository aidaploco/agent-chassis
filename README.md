# agent-chassis

Runtime **chassis** for role-based AI Employees. An "Employee" is a declarative
**role manifest** (one YAML file) plus reused **capability packs** — never a new
codebase. The chassis provides everything an Employee needs to run in
production: durable execution (Temporal), channels (HTTP, Slack-shaped),
guardrails, memory, observability, escalation-with-human-approval, and
self-introspection. A role provides only *configuration*.

> **New here? Read this file top to bottom, then open [`DESIGN.md`](DESIGN.md).**
> Every design decision in the code is numbered (`D1`…`D24`) and lives in
> `DESIGN.md`. When a comment says "(D13)", that's a pointer to the full
> rationale there. A [glossary of the ones you'll meet most](#glossary-of-d-references)
> is at the bottom of this file.

---

## The one big idea

Onboarding a new Employee should be **new YAML, not new code.** Everything that
varies between Employees — persona, which capabilities they have, how they
route between specialists, which guardrails run and at what thresholds, what
they escalate, what they're scheduled to do — is expressed in a manifest. The
Python in `src/` is the *chassis*: it is identical for every Employee and never
mentions any specific one.

Two real Employees ship as permanent test fixtures in [`examples/`](examples/):

- **Lana** — a customer-service Employee (billing + support + a routing
  supervisor). Proves the chassis can carry a real multi-agent conversational role.
- **Nova** — an ops/reporting Employee that runs on a schedule and asks a human
  before publishing. Proves a second, very different role shape needs zero
  chassis changes.

---

## Architecture at a glance

```
                 ┌─────────────────────────────────────────────────┐
   HTTP / Slack  │  channels/    normalize wire format → envelope   │
   ────────────► │               (the core never learns the channel)│
                 └───────────────────────┬─────────────────────────┘
                                         │  MessageEnvelope
                                         ▼
                 ┌─────────────────────────────────────────────────┐
                 │  durable/     Temporal workflow shell (vendor-   │
                 │               free): turn queue → guardrails →   │
                 │               routing loop → escalation → outcome│
                 └───────┬───────────────────────────┬─────────────┘
                         │ guardrail pre-flight       │ run_turn (adapter seam)
                         ▼                            ▼
                 ┌───────────────┐          ┌───────────────────────┐
                 │  packs/       │          │  adapters/pydantic_ai │
                 │  guardrails,  │          │  the ONLY place that  │
                 │  capabilities │          │  imports the vendor   │
                 └───────────────┘          │  agent SDK (D2)       │
                                            └───────────────────────┘
   Everything above speaks only chassis-owned types from  contracts/ .
```

The load-bearing principle is the **adapter seam (D2)**: the chassis owns all
the deterministic orchestration and depends only on the vendor-free types in
[`contracts/`](src/agent_chassis/contracts/). Only code under
[`adapters/`](src/agent_chassis/adapters/) may import the agent framework
(`pydantic_ai`). This is not a convention — it is enforced in CI by
import-linter (`uv run lint-imports`). Swapping the runtime out for a different
agent SDK is a new adapter, not a chassis rewrite.

---

## The lifecycle of one message

Following a single HTTP chat message end-to-end is the fastest way to learn the
codebase. In order:

1. **`channels/http.py`** receives `POST /v1/messages`, builds a
   `MessageEnvelope`, and does an **update-with-start (D3)**: the first message
   for a conversation starts a `ConversationWorkflow`; later ones attach to the
   running one.
2. **`durable/workflows.py` — `ConversationWorkflow`** enqueues the message on
   an in-workflow **FIFO turn queue (D13)** so concurrent messages never
   interleave, and a single consumer processes one turn at a time.
3. **Guardrail pre-flight (D5)**: each configured guardrail (`packs/guardrails.py`)
   runs as its own activity. A trip short-circuits the turn with a refusal.
4. **Routing loop (D4)**: the current agent runs a turn via the adapter seam.
   The agent returns a structured **directive** — `reply`, `route`, `escalate`,
   or `needs_approval`. The chassis walks the manifest's `routing` graph
   deterministically; illegal or over-budget routes are errors, never silent
   fallbacks.
5. **`adapters/pydantic_ai/adapter.py` — `run_turn`** is where the vendor agent
   actually executes. Inside a workflow, its model calls and tool calls each
   become their own Temporal activity (automatic, via `TemporalAgent`).
6. **Reply** returns up through the update result (sync channels like HTTP) or
   is pushed via an outbound activity (async channels like Slack, D15).
7. When the conversation goes idle, the workflow emits one **`OutcomeEvent`
   (D12)** — the public metrics contract — and completes.

Scheduled work (Nova) takes a parallel path through `TaskWorkflow`, which adds
the durable human-approval state machine (D10): a task can pause on an
approval-gated tool and wait *days* for a signed decision.

---

## Repository map

Start at the seam (`contracts/`), then read outward.

| Path | What lives here |
|---|---|
| [`src/agent_chassis/`](src/agent_chassis/) | The chassis package. Every subfolder has its own README. |
| ├ [`contracts/`](src/agent_chassis/contracts/) | The vendor-free types every seam speaks. **Read first.** |
| ├ [`manifest/`](src/agent_chassis/manifest/) | Role manifest schema v1 + fail-closed loader (D7). |
| ├ [`packs/`](src/agent_chassis/packs/) | The reuse mechanism: capability & guardrail packs + registry. |
| ├ [`adapters/`](src/agent_chassis/adapters/) | Runtime adapters. The **only** vendor-importing code (D2). |
| ├ [`durable/`](src/agent_chassis/durable/) | Temporal workflows, activities, codec, schedules. The orchestrator. |
| ├ [`channels/`](src/agent_chassis/channels/) | HTTP and Slack-shaped adapters; inbound/outbound. |
| ├ [`memory/`](src/agent_chassis/memory/) | Retrieval + write-back substrate (Postgres + pgvector). |
| ├ [`evals/`](src/agent_chassis/evals/) | The eval contract (D17): case schema, runner, graders. |
| ├ [`utils/`](src/agent_chassis/utils/) | Vendor-agnostic LLM helper and logging. |
| ├ `config.py` | All environment configuration (the deployment contract). |
| ├ `role_loader.py` | Manifest → registry → resolved role. Shared by both processes. |
| ├ `api.py` | HTTP process entrypoint (`uvicorn agent_chassis.api:app`). |
| ├ `worker.py` | Temporal worker entrypoint (`python -m agent_chassis.worker`). |
| ├ `introspection.py` | An Employee's "what can I do / how have I done" self-description. |
| └ `observability.py` | OpenTelemetry/Phoenix tracing setup (fail-soft). |
| [`examples/`](examples/) | Lana, Nova, and the walking-skeleton role — permanent fixtures. |
| [`tests/`](tests/) | Unit + integration tests; the CI coverage gate lives here. |
| [`scripts/`](scripts/) | Operational scripts (e.g. reproducible image builds). |
| [`DESIGN.md`](DESIGN.md) | The numbered decision log (ADRs). The source of truth. |
| [`PATCHES.md`](PATCHES.md) | Active `workflow.patched()` ledger (D14). |

A role deployment runs as **two processes off one image** (D18): an HTTP `api`
unit and a Temporal `worker` unit, both pointed at the same manifest via
`MANIFEST_PATH`.

---

## Quickstart

### Run the test suite

```sh
uv sync                                        # install (Python 3.13+)
uv run pytest --cov                            # tests + coverage
uv run ruff format --check . && uv run ruff check .
uv run lint-imports                            # the adapter seam contract (D2)
```

### Run an Employee locally

`docker-compose.yml` brings up Temporal, Postgres+pgvector, and both chassis
units. It runs the **walking-skeleton** ("echo") role by default; point
`ROLE_DIR` at another example to switch.

```sh
# The worker fails closed without a payload-encryption key (D9):
echo "TEMPORAL_PAYLOAD_CODEC_KEY=$(openssl rand -base64 32)" >> .env
echo "OPENAI_API_KEY=sk-..."                                >> .env

docker compose up --build                      # echo demo
ROLE_DIR=./examples/lana docker compose up --build   # or Lana

curl -s localhost:8001/v1/messages \
  -H 'content-type: application/json' \
  -d '{"conversation_id":"c1","message":"reverse the word chassis"}'
```

See [`.env.example`](.env.example) for every knob and
[`config.py`](src/agent_chassis/config.py) for the authoritative list.

### Model tiers

Manifests name **abstract tiers** (`light` / `standard` / `frontier`), never a
concrete model. The mapping is a *deployment* concern set via `MODEL_TIER_*`
env vars, which accept any pydantic-ai model string — e.g.
`openai:gpt-5.4-mini-2026-03-17` or `anthropic:claude-opus-4-8`. `frontier` has
no default: a manifest requesting it fails loud until the deployment sets one.
This keeps roles portable across providers and model generations. Chassis-side
LLM calls (guardrails, the eval judge) use the same provider-prefixed strings
through [`utils/llm.py`](src/agent_chassis/utils/llm.py), never the agent
framework.

---

## Writing a new Employee

1. Write a `manifest.yaml` (copy [`examples/skeleton/manifest.yaml`](examples/skeleton/manifest.yaml)
   and grow it; the schema is in [`manifest/schema.py`](src/agent_chassis/manifest/schema.py)).
2. If you need new tools, write a **capability pack** — a `CapabilityPack`
   subclass in a `packs/` directory, exposing a module-level `PACKS` list. See
   [`examples/lana/packs/billing.py`](examples/lana/packs/billing.py).
3. Point `MANIFEST_PATH` (and `PACKS_DIR`, for tenant-custom packs) at them.
4. Bundle eval cases with the pack; CI gates on the per-role pass rate (D17).

The loader is **fail-closed (D7)**: unknown fields, illegal routes, undeclared
capabilities, and out-of-bounds guardrail thresholds are all validation errors
that name the offending path — not silent no-ops.

---

## Glossary of `D` references

The code cites these constantly. Full text and rejected alternatives are in
[`DESIGN.md`](DESIGN.md); this is the two-line version.

| Ref | In one line |
|---|---|
| **D1** | Runtime is pydantic-ai wrapped by Temporal's `TemporalAgent` (per-call durability). |
| **D2** | The adapter seam: chassis is vendor-free; only `adapters/` imports the SDK. Enforced in CI. |
| **D3** | Messages enter via Temporal **update-with-start**, so a reply can ride the result. |
| **D4** | Routing is chassis-owned: agents emit structured directives; the chassis walks the graph. |
| **D5** | Input guardrails run as pre-flight activities; output checks as validators. |
| **D6** | Two workflow types — `ConversationWorkflow` (long-lived) and `TaskWorkflow` (scheduled). |
| **D6a** | Conversation history *is* workflow state, carried through continue-as-new. No session store. |
| **D7** | Manifest schema v1: versioned, `extra="forbid"`, naming-stable identifiers, fail-closed. |
| **D8** | Memory substrate = retrieval + write-back behind one interface (pgvector today). |
| **D9** | Payload encryption fail-closed: no codec key ⇒ the worker refuses to start. |
| **D10** | Human escalation is a first-class workflow state that can durably wait days. |
| **D11** | One append-only audit log for manifest overrides and approval decisions. |
| **D12** | Every completed conversation/task emits one versioned `OutcomeEvent` (public contract). |
| **D13** | Inbound turns are serialized through an in-workflow FIFO queue. |
| **D14** | Workflow code changes ship behind `workflow.patched()`; ledger in `PATCHES.md`. |
| **D15** | Outbound delivery is at-least-once + idempotent per structured delivery key. |
| **D16** | Single tenant per deployment; isolation is the deployment environment's job, `tenant` is a label. |
| **D17** | Evals are chassis machinery: versioned case schema, full-conversation binary judge. |
| **D18** | Deploys as two units (HTTP + worker) off one image. |
| **D21** | Channel auth: the audited identity is the authenticated principal, never caller-supplied. |
| **D22–D24** | Memory evolution: provenance, then scoped retrieval (design-first). |

---

## Development conventions

- **Python 3.13**, managed with `uv`. Formatting and linting via `ruff`.
- **The seam is sacred.** If a new chassis package is added, it must be listed
  in the import-linter contract in [`pyproject.toml`](pyproject.toml) so it too
  is forbidden from importing the vendor SDK.
- **Fail closed, fail loud.** Missing encryption key, unknown manifest field,
  unattributable approval — these stop the process or return an error; they are
  never silently degraded.
- **Zero role special-casing.** No chassis code branches on a specific
  Employee's name; a test scans the source to enforce it.
