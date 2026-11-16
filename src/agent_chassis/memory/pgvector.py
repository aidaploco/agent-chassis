import json
import re
import uuid
from datetime import UTC, datetime

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from agent_chassis.memory.base import (
    DEFAULT_AUTHOR,
    DEFAULT_SOURCE,
    Embedder,
    MemoryEntry,
)
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)

# Reciprocal-rank-fusion constant (the conventional 60): each arm contributes
# 1 / (_RRF_K + rank) per hit, so no arm can crowd the other out of the page.
_RRF_K = 60
_MAX_KEYWORD_TERMS = 8
_MIN_TERM_LENGTH = 3
# Words that appear in almost every entry. As ILIKE terms they match nearly
# everything, and because keyword hits win rank ties, "what did we decide
# about the refund" would surface recent entries containing "the" ahead of
# the semantically relevant ones.
_STOP_WORDS = frozenset(
    "about after all also and any are because been before but can could did "  # noqa: SIM905 - a word list reads better as one string
    "does doing done for from had has have her him his how into its just may "
    "might more most must not now off once only other our out over she should "
    "than that the their them then there these they this those too under "
    "very was were what when where which while who whom why will with would "
    "you your".split()
)


def _keyword_patterns(query: str) -> list[str]:
    """ILIKE patterns for the keyword arm: one per distinct query term.

    Matching the whole query as a single substring only ever hit when the
    query appeared verbatim in an entry. LIKE wildcards in the query are
    escaped so "100%" means the literal text.
    """
    terms: list[str] = []
    for raw in re.split(r"\s+", query.lower()):
        term = raw.strip(".,;:!?\"'()[]{}")
        if (
            len(term) >= _MIN_TERM_LENGTH
            and term not in _STOP_WORDS
            and term not in terms
        ):
            terms.append(term)
    if not terms and query.strip():
        terms = [query.strip().lower()]  # nothing distinctive: match the phrase
    return [
        "%" + re.sub(r"([\\%_])", r"\\\1", term) + "%"
        for term in terms[:_MAX_KEYWORD_TERMS]
    ]


class PgVectorSubstrate:
    """MemorySubstrate over Postgres + pgvector.

    Retrieval merges two arms: cosine similarity over embeddings (semantic)
    and per-term ILIKE match (keyword), fused by reciprocal rank so a hit
    found by both arms ranks first and neither arm can crowd the other out.
    One table shared by all collections; the collection column namespaces
    employees.
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
        # Composed, never interpolated: the table name is quoted as an
        # identifier, so it cannot inject SQL however it is spelled.
        self._t = sql.Identifier(table)
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
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        id UUID PRIMARY KEY,
                        collection TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}',
                        embedding vector({dim}) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        source TEXT NOT NULL DEFAULT {source},
                        author TEXT NOT NULL DEFAULT {author}
                    )
                    """
                ).format(
                    table=self._t,
                    dim=sql.Literal(self._embedder.dim),
                    source=sql.Literal(DEFAULT_SOURCE),
                    author=sql.Literal(DEFAULT_AUTHOR),
                )
            )
            # D23 migration: pre-provenance installs gain the columns with
            # the least-trust default — unknown origin is never promoted.
            await conn.execute(
                sql.SQL(
                    "ALTER TABLE {table}"
                    " ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT {source},"
                    " ADD COLUMN IF NOT EXISTS author TEXT NOT NULL DEFAULT {author}"
                ).format(
                    table=self._t,
                    source=sql.Literal(DEFAULT_SOURCE),
                    author=sql.Literal(DEFAULT_AUTHOR),
                )
            )
            await conn.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index} ON {table} (collection, kind)"
                ).format(
                    index=sql.Identifier(f"{self._table}_collection_idx"),
                    table=self._t,
                )
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
                sql.SQL(
                    "INSERT INTO {table}"
                    " (id, collection, kind, content, metadata, embedding,"
                    "  created_at, source, author)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(table=self._t),
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
        kind_clause = sql.SQL("AND kind = ANY(%s)" if kinds else "")
        patterns = _keyword_patterns(query)

        async with self._pool.connection() as conn:
            semantic_params: list = [embedding, self._collection]
            if kinds:
                semantic_params.append(kinds)
            semantic_params.extend([embedding, limit])
            semantic = await (
                await conn.execute(
                    sql.SQL(
                        """
                        SELECT id, collection, kind, content, metadata, created_at,
                               source, author,
                               1 - (embedding <=> %s::vector) AS score
                        FROM {table}
                        WHERE collection = %s {kind_clause}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """
                    ).format(table=self._t, kind_clause=kind_clause),
                    semantic_params,
                )
            ).fetchall()

            keyword: list = []
            if patterns:
                keyword_params: list = [patterns, self._collection, patterns]
                if kinds:
                    keyword_params.append(kinds)
                keyword_params.append(limit)
                keyword = await (
                    await conn.execute(
                        sql.SQL(
                            """
                            SELECT id, collection, kind, content, metadata,
                                   created_at, source, author,
                                   NULL AS score,
                                   (SELECT count(*) FROM unnest(%s::text[]) AS p
                                     WHERE content ILIKE p) AS matched
                            FROM {table}
                            WHERE collection = %s AND content ILIKE ANY(%s)
                                  {kind_clause}
                            ORDER BY matched DESC, created_at DESC
                            LIMIT %s
                            """
                        ).format(table=self._t, kind_clause=kind_clause),
                        keyword_params,
                    )
                ).fetchall()

        # Reciprocal rank fusion. The old merge appended keyword rows after a
        # semantic arm that always filled `limit`, then truncated — so keyword
        # hits never surfaced once a collection held `limit` entries.
        rows: dict[str, tuple] = {}
        fused: dict[str, float] = {}
        for arm in (semantic, keyword):
            for rank, row in enumerate(arm, start=1):
                entry_id = str(row[0])
                rows.setdefault(entry_id, row)  # keeps the semantic score
                fused[entry_id] = fused.get(entry_id, 0.0) + 1 / (_RRF_K + rank)
        keyword_ids = {str(row[0]) for row in keyword}
        # Ties go to literal term matches — the rarer, more specific signal.
        ordered = sorted(fused, key=lambda i: (-fused[i], i not in keyword_ids))
        return [
            MemoryEntry(
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
            for entry_id in ordered[:limit]
            for row in (rows[entry_id],)
        ]

    async def recent(
        self, kinds: list[str] | None = None, limit: int = 5
    ) -> list[MemoryEntry]:
        kind_clause = sql.SQL("AND kind = ANY(%s)" if kinds else "")
        params: list = [self._collection]
        if kinds:
            params.append(kinds)
        params.append(limit)
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    sql.SQL(
                        """
                        SELECT id, collection, kind, content, metadata, created_at,
                               source, author
                        FROM {table}
                        WHERE collection = %s {kind_clause}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """
                    ).format(table=self._t, kind_clause=kind_clause),
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
        except Exception:  # noqa: BLE001 - a health check reports, it never raises
            return False
