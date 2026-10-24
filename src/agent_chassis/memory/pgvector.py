import json
import uuid
from datetime import UTC, datetime

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agent_chassis.memory.base import (
    DEFAULT_AUTHOR,
    DEFAULT_SOURCE,
    Embedder,
    MemoryEntry,
)
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


class PgVectorSubstrate:
    """MemorySubstrate over Postgres + pgvector.

    Retrieval merges two arms: cosine similarity over embeddings (semantic)
    and ILIKE match (keyword), deduplicated, semantic-first. One table shared
    by all collections; the collection column namespaces employees.
    """

    def __init__(
        self,
        dsn: str,
        collection: str,
        embedder: Embedder,
        table: str = "chassis_memory",
    ) -> None:
        self._dsn = dsn
        self._collection = collection
        self._embedder = embedder
        self._table = table
        self._pool = AsyncConnectionPool(
            dsn, open=False, kwargs={"autocommit": True}, configure=self._configure
        )

    @staticmethod
    async def _configure(conn) -> None:
        await register_vector_async(conn)

    async def start(self) -> None:
        # The pool registers the vector type on every connection, so the
        # extension must exist before the first pooled connection opens.
        plain = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        try:
            await plain.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await plain.close()
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    id UUID PRIMARY KEY,
                    collection TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}',
                    embedding vector({self._embedder.dim}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    source TEXT NOT NULL DEFAULT '{DEFAULT_SOURCE}',
                    author TEXT NOT NULL DEFAULT '{DEFAULT_AUTHOR}'
                )
                """
            )
            # D23 migration: pre-provenance installs gain the columns with
            # the least-trust default — unknown origin is never promoted.
            await conn.execute(
                f"ALTER TABLE {self._table}"
                f" ADD COLUMN IF NOT EXISTS source TEXT NOT NULL"
                f" DEFAULT '{DEFAULT_SOURCE}',"
                f" ADD COLUMN IF NOT EXISTS author TEXT NOT NULL"
                f" DEFAULT '{DEFAULT_AUTHOR}'"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._table}_collection_idx"
                f" ON {self._table} (collection, kind)"
            )
        _logger.info("memory substrate ready collection=%s", self._collection)

    async def close(self) -> None:
        await self._pool.close()

    async def record(
        self,
        kind: str,
        content: str,
        metadata: dict[str, str] | None = None,
        *,
        source: str,
        author: str,
    ) -> str:
        entry_id = str(uuid.uuid4())
        embedding = await self._embedder.embed(content)
        async with self._pool.connection() as conn:
            await conn.execute(
                f"INSERT INTO {self._table}"
                " (id, collection, kind, content, metadata, embedding,"
                "  created_at, source, author)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry_id,
                    self._collection,
                    kind,
                    content,
                    json.dumps(metadata or {}),
                    embedding,
                    datetime.now(UTC),
                    source,
                    author,
                ),
            )
        return entry_id

    async def retrieve(
        self, query: str, limit: int = 5, kinds: list[str] | None = None
    ) -> list[MemoryEntry]:
        embedding = await self._embedder.embed(query)
        kind_clause = "AND kind = ANY(%s)" if kinds else ""

        async with self._pool.connection() as conn:
            semantic_params: list = [embedding, self._collection]
            if kinds:
                semantic_params.append(kinds)
            semantic_params.append(limit)
            semantic = await (
                await conn.execute(
                    f"""
                    SELECT id, collection, kind, content, metadata, created_at,
                           source, author,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {self._table}
                    WHERE collection = %s {kind_clause}
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    semantic_params,
                )
            ).fetchall()

            keyword_params: list = [self._collection, f"%{query}%"]
            if kinds:
                keyword_params.append(kinds)
            keyword_params.append(limit)
            keyword = await (
                await conn.execute(
                    f"""
                    SELECT id, collection, kind, content, metadata, created_at,
                           source, author,
                           NULL AS score
                    FROM {self._table}
                    WHERE collection = %s AND content ILIKE %s {kind_clause}
                    LIMIT %s
                    """,
                    keyword_params,
                )
            ).fetchall()

        entries: dict[str, MemoryEntry] = {}
        for row in [*semantic, *keyword]:
            entry_id = str(row[0])
            if entry_id not in entries:
                entries[entry_id] = MemoryEntry(
                    id=entry_id,
                    collection=row[1],
                    kind=row[2],
                    content=row[3],
                    metadata=row[4] or {},
                    created_at=row[5],
                    source=row[6],
                    author=row[7],
                    score=float(row[8]) if row[8] is not None else None,
                )
        return list(entries.values())[:limit]

    async def recent(
        self, kinds: list[str] | None = None, limit: int = 5
    ) -> list[MemoryEntry]:
        kind_clause = "AND kind = ANY(%s)" if kinds else ""
        params: list = [self._collection]
        if kinds:
            params.append(kinds)
        params.append(limit)
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT id, collection, kind, content, metadata, created_at,
                           source, author
                    FROM {self._table}
                    WHERE collection = %s {kind_clause}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
            ).fetchall()
        return [
            MemoryEntry(
                id=str(r[0]),
                collection=r[1],
                kind=r[2],
                content=r[3],
                metadata=r[4] or {},
                created_at=r[5],
                source=r[6],
                author=r[7],
            )
            for r in rows
        ]

    async def ping(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False
