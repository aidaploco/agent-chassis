# `agent_chassis` — the chassis package

This is the entire runtime chassis. It is **identical for every Employee** and
never references any specific one — a role is data (a manifest + packs), not
code that lives here.

## Two entrypoints, one image

A deployed role runs as two processes built from the same image (D18):

| Entrypoint | Command | Role |
|---|---|---|
| [`api.py`](api.py) | `uvicorn agent_chassis.api:app` | Hosts the HTTP + Slack channels; talks to Temporal as a client. |
| [`worker.py`](worker.py) | `python -m agent_chassis.worker` | Runs the Temporal workflows and activities — where turns actually execute. |

Both call [`role_loader.py`](role_loader.py) to turn `MANIFEST_PATH` into a
resolved role, and read all configuration from [`config.py`](config.py).

## How the packages relate

```
contracts/     the vendor-free type vocabulary  ── everything below depends on it
   ▲
manifest/      validates the role YAML into typed specs
packs/         capability + guardrail packs, and the registry that resolves them
   ▲
adapters/      the runtime seam's ONE vendor implementation (pydantic-ai)
durable/       the Temporal workflow shell that orchestrates a turn
channels/      normalize HTTP/Slack ⇄ MessageEnvelope
memory/        retrieval + write-back substrate
evals/         the behavioral regression contract
utils/         vendor-agnostic LLM calls + logging
```

Top-level modules that don't warrant a folder:

- [`config.py`](config.py) — `ChassisSettings`, every env var, the tier→model map.
- [`role_loader.py`](role_loader.py) — shared manifest→registry→`ResolvedRole` path.
- [`introspection.py`](introspection.py) — the `/v1/self` self-description and the `describe_self` builtin tool.
- [`observability.py`](observability.py) — OTLP/Phoenix tracing setup (fail-soft; no-ops without a collector).

## Reading order for a newcomer

1. [`contracts/`](contracts/) — learn the nouns (`MessageEnvelope`, `TurnDirective`, `OutcomeEvent`).
2. [`manifest/schema.py`](manifest/schema.py) — see what a role can declare.
3. [`durable/workflows.py`](durable/) — `ConversationWorkflow._process` is the spine of a turn.
4. [`adapters/pydantic_ai/adapter.py`](adapters/pydantic_ai/) — where the agent actually runs.

Every folder below has its own README explaining what it owns and why.
