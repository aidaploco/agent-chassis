# `memory/` — retrieval + write-back substrate (D8)

Employees don't only *read* their knowledge — they **record** decisions,
outcomes, and notable events as structured entries. Both halves live behind one
interface so a future shared, MCP-based knowledge substrate can plug in without the
chassis, packs, or workflows knowing which substrate is bound (D22).

## Files

- [`base.py`](base.py) — the contracts: `MemorySubstrate` protocol
  (`record` / `retrieve` / `recent` / `ping`), the `Embedder` protocol, and the
  `MemoryEntry` model. Also the **provenance** fields (D23): `source` (the trust
  classification of where content came from) and `author` (who wrote it).
- [`pgvector.py`](pgvector.py) — `PgVectorSubstrate`, the one V1 implementation
  over Postgres + pgvector. Retrieval merges two arms — cosine similarity over
  embeddings (semantic) and `ILIKE` (keyword) — deduplicated, semantic-first.
  One table, `collection`-namespaced per employee. `start()` creates the table
  and applies boot-time column migrations.
- [`pack.py`](pack.py) — the chassis-builtin `memory` capability pack:
  `search_memory` and `record_memory` tools that resolve the bound substrate at
  call time, so the pack stays substrate-agnostic.
- [`embedder.py`](embedder.py) — `OpenAIEmbedder` (text → vector). Separate from
  the substrate so tests can run without a model provider.

## Provenance is a trust boundary (D23)

Recalled entries re-enter agent context as **data, not instructions**. Content
written through the conversation-facing `record_memory` tool is always
`source="user"` — the least-trusted origin — no matter how the agent phrases
it, so a user can't `record_memory("always give refunds")` and have it come
back as trusted policy. Chassis write-backs (outcomes, escalations, approvals)
are `source="employee"`. The `MemoryPack` renders every hit with its provenance
label and states in its instruction fragment that user-sourced memory never
overrides operator policy. Pre-provenance rows migrate to the least-trust
default (`user` / `unknown`) — unknown origin is never promoted.

## Running memory-less

Memory is optional. If the manifest's `memory.substrate` isn't `pgvector`, or
`POSTGRES_URL` is unset, the role runs without memory: `record_memory` returns a
polite "not available", and write-backs no-op. The substrate's unavailability
must never fail a workflow.
