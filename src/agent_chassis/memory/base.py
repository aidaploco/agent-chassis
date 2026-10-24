from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Provenance sources (D23). "connector:<name>" marks ingestion by a named
# connector (Mem-3); the migration default for pre-provenance rows is "user"
# — unknown origin gets the least trust, never more.
PROVENANCE_SOURCES = ("operator", "employee", "user")
_CONNECTOR_PREFIX = "connector:"
DEFAULT_SOURCE = "user"
DEFAULT_AUTHOR = "unknown"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    collection: str
    kind: str  # knowledge | decision | outcome | event
    content: str
    # Provenance (D23): source is the trust classification of where the
    # content originated; author is the identity that performed the write.
    # Recalled entries re-enter agent context as DATA labeled with these —
    # user-sourced content never carries operator authority.
    source: str = DEFAULT_SOURCE
    author: str = DEFAULT_AUTHOR
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    score: float | None = None  # retrieval relevance, when applicable

    @field_validator("source")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        if v in PROVENANCE_SOURCES or (
            v.startswith(_CONNECTOR_PREFIX) and len(v) > len(_CONNECTOR_PREFIX)
        ):
            return v
        raise ValueError(
            f"source must be one of {PROVENANCE_SOURCES} or 'connector:<name>',"
            f" got {v!r}"
        )


class Embedder(Protocol):
    """Text → vector. Kept separate so tests run without a model provider."""

    dim: int

    async def embed(self, text: str) -> list[float]: ...


class MemorySubstrate(Protocol):
    """The two halves of employee memory (D8): retrieval and WRITE-BACK.

    FUTURE SEAM: a shared, MCP-based knowledge substrate implements this same
    protocol — the chassis, packs, and workflows never know which substrate
    is bound; the manifest's ``memory.substrate`` field chooses.
    """

    async def record(
        self,
        kind: str,
        content: str,
        metadata: dict[str, str] | None = None,
        *,
        source: str,
        author: str,
    ) -> str:
        """Persist one structured entry with required provenance (D23).

        Args:
            kind: The entry category — ``knowledge`` | ``decision`` |
                ``outcome`` | ``event``.
            content: The text to store (and embed for semantic search).
            metadata: Optional string→string tags (e.g. conversation id).
            source: Trust classification of where the content came from —
                ``operator`` | ``employee`` | ``user`` | ``connector:<name>``.
                Keyword-only and required; a user-sourced write can never claim
                more trust than ``user``.
            author: The identity that performed the write (the employee's
                manifest name for chassis write-backs). Keyword-only, required.

        Returns:
            The new entry's id.
        """
        ...

    async def retrieve(
        self, query: str, limit: int = 5, kinds: list[str] | None = None
    ) -> list[MemoryEntry]:
        """Semantic + keyword search over this employee's collection.

        Every hit carries its provenance (D23).

        Args:
            query: The search text (embedded for the semantic arm, matched
                literally for the keyword arm).
            limit: Maximum number of entries to return.
            kinds: If given, restrict results to these kinds.
        """
        ...

    async def recent(
        self, kinds: list[str] | None = None, limit: int = 5
    ) -> list[MemoryEntry]:
        """Newest entries first — the introspection feed.

        Args:
            kinds: If given, restrict to these kinds (e.g. ``["outcome"]``).
            limit: Maximum number of entries to return.
        """
        ...

    async def ping(self) -> bool:
        """Cheap health check; ``True`` if the substrate is reachable."""
        ...
