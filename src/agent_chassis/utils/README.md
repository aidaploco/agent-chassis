# `utils/` — vendor-agnostic helpers

Small, dependency-light helpers the rest of the chassis leans on.

- [`llm.py`](llm.py) — `complete()`, a single-shot completion helper for
  **chassis-side** LLM calls that must survive a runtime swap: guardrail
  verdicts (D5) and the eval judge (D17). These deliberately do **not** go
  through the agent adapter (the import-linter forbids `pydantic_ai` here). It
  takes the same provider-prefixed model strings as the tier table
  (`openai:…`, `anthropic:…`; a bare name means openai), runs one system+user
  turn and returns text. One client per provider per process. Temperature is
  pinned to 0 on OpenAI only: the Anthropic SDK (2.x) no longer accepts a
  `temperature`, so Anthropic verdicts use the provider default. Both provider
  paths are tested against mock transports, and the Anthropic path was also run
  live (2026-11-16): Lana's 17-case eval suite passed 3 runs each with the tiers,
  the guardrails and the judge all on Anthropic (see DESIGN D1).
- [`idempotency.py`](idempotency.py) — `idempotency_key()`, a key that is the
  same on every retry of one tool call and different for every other call, so a
  tool can make its side effect safe to repeat (tools run at-least-once; D1).
  Re-exported from `agent_chassis.packs`, which is where pack authors import it.
- [`logger.py`](logger.py) — `get_logger(name)`, a stdout logger configured once
  under the `agent_chassis` root. A module run as a script
  (`python -m agent_chassis.worker`, whose `__name__` is `__main__`) is placed
  under that root too; before that, the worker's INFO lines were dropped.

Why this exists as a seam: a guardrail pack is chassis-side and must keep
working if the agent runtime is swapped out, so it can't reach for the agent
framework to make an LLM call. `complete()` is the provider-agnostic escape
hatch for exactly those calls.
