"""Memory substrate (D8): retrieval + write-back.

Employees don't only read their knowledge — they record decisions, outcomes,
and notable events as structured entries. This interface is where a shared,
MCP-based knowledge substrate plugs in later; V1 implements it once over
Postgres + pgvector.
"""

from agent_chassis.memory.base import Embedder, MemoryEntry, MemorySubstrate

__all__ = ["Embedder", "MemoryEntry", "MemorySubstrate"]
