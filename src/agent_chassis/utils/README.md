# `utils/` — vendor-agnostic helpers

Small, dependency-light helpers the rest of the chassis leans on.

- [`llm.py`](llm.py) — `complete()`, a single-shot completion helper for
  **chassis-side** LLM calls that must survive a runtime swap: guardrail
  verdicts (D5) and the eval judge (D17). These deliberately do **not** go
  through the agent adapter (the import-linter forbids `pydantic_ai` here). It
  takes the same provider-prefixed model strings as the tier table
  (`openai:…`, `anthropic:…`; a bare name means openai), runs one system+user
  turn at temperature 0, and returns text. One client per provider per process.
- [`logger.py`](logger.py) — `get_logger(name)`, a stdout logger configured once
  under the `agent_chassis` root.

Why this exists as a seam: a guardrail pack is chassis-side and must keep
working if the agent runtime is swapped out, so it can't reach for the agent
framework to make an LLM call. `complete()` is the provider-agnostic escape
hatch for exactly those calls.
