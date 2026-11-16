"""PgVectorSubstrate against a real dockerized Postgres+pgvector.

The embedder is a deterministic fake (word-presence vector + bias term), so
semantic ranking is testable without a model provider.
"""

import uuid

import psycopg
import pytest

from agent_chassis.memory.pgvector import PgVectorSubstrate

_WORDS = ["billing", "invoice", "cats", "weather"]


class FakeEmbedder:
    dim = len(_WORDS) + 1  # +1 bias so no vector is ever zero

    async def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        return [1.0 if word in lowered else 0.0 for word in _WORDS] + [0.1]


@pytest.fixture
async def substrate(postgres_url):
    store = PgVectorSubstrate(
        postgres_url, collection=f"c{uuid.uuid4().hex[:8]}", embedder=FakeEmbedder()
    )
    await store.start()
    yield store
    await store.close()


async def test_record_and_semantic_retrieve(substrate):
    await substrate.record(
        "decision",
        "Customer prefers annual billing invoices.",
        source="user",
        author="lana",
    )
    await substrate.record(
        "knowledge", "Cats are wonderful animals.", source="user", author="lana"
    )

    entries = await substrate.retrieve("question about billing invoice", limit=1)

    assert len(entries) == 1
    assert "billing" in entries[0].content
    assert entries[0].score is not None and entries[0].score > 0.5


async def test_keyword_arm_finds_semantic_misses(substrate):
    # "SLA" shares no embedder words with the query vector — only the
    # keyword arm can find it.
    await substrate.record(
        "knowledge",
        "Enterprise SLA is 99.99% uptime.",
        source="operator",
        author="ops",
    )

    entries = await substrate.retrieve("SLA", limit=5)

    assert any("99.99%" in e.content for e in entries)


async def test_keyword_hit_survives_a_full_semantic_page(substrate):
    """Regression: the semantic arm always fills `limit`, and keyword rows
    used to be appended after it and truncated away. The entry only the
    keyword arm can find must still make the page."""
    for n in range(3):
        await substrate.record(
            "knowledge", f"Cats fact {n}.", source="operator", author="ops"
        )
    await substrate.record(
        "knowledge",
        "Enterprise SLA is 99.99% uptime.",
        source="operator",
        author="ops",
    )

    # Semantically this query is about cats; "SLA" and "uptime" are literal.
    entries = await substrate.retrieve("cats SLA uptime?", limit=3)

    assert len(entries) == 3
    assert any("99.99%" in e.content for e in entries)


def test_keyword_wildcards_are_literal():
    from agent_chassis.memory.pgvector import _keyword_patterns

    assert _keyword_patterns("20%") == ["%20\\%%"]  # % escaped, not a wildcard
    assert _keyword_patterns("a an") == ["%a an%"]  # no usable term: whole query
    assert _keyword_patterns("SLA sla Sla?") == ["%sla%"]  # deduplicated
    # Stop words match nearly every entry and would outrank real hits on ties.
    assert _keyword_patterns("what did we decide about the refunds for Acme?") == [
        "%decide%",
        "%refunds%",
        "%acme%",
    ]
    assert _keyword_patterns("what is the") == ["%what is the%"]  # all stop words


async def test_kind_filter(substrate):
    await substrate.record(
        "decision", "We chose invoice net-30 terms.", source="user", author="lana"
    )
    await substrate.record(
        "event", "Invoice system had an outage.", source="employee", author="lana"
    )

    entries = await substrate.retrieve("invoice", kinds=["decision"], limit=5)

    assert entries and all(e.kind == "decision" for e in entries)


async def test_collections_are_isolated(substrate, postgres_url):
    other = PgVectorSubstrate(
        postgres_url, collection="other_employee", embedder=FakeEmbedder()
    )
    await other.start()
    try:
        await other.record(
            "knowledge",
            "Secret billing data of another employee.",
            source="employee",
            author="other",
        )
        entries = await substrate.retrieve("billing", limit=10)
        assert all("another employee" not in e.content for e in entries)
    finally:
        await other.close()


async def test_ping(substrate):
    assert await substrate.ping() is True


async def test_provenance_round_trip(substrate):
    await substrate.record(
        "knowledge", "Billing runs monthly.", source="operator", author="aida"
    )

    entries = await substrate.retrieve("billing", limit=5)

    hit = next(e for e in entries if "monthly" in e.content)
    assert hit.source == "operator" and hit.author == "aida"


async def test_migration_defaults_pre_provenance_rows_to_least_trust(postgres_url):
    """A pre-D23 install has no source/author columns; start() must add
    them and existing rows must read back with the least-trust default."""
    table = f"chassis_mig_{uuid.uuid4().hex[:8]}"
    conn = psycopg.connect(postgres_url, autocommit=True)
    embedder = FakeEmbedder()
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"""
            CREATE TABLE {table} (
                id UUID PRIMARY KEY,
                collection TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}',
                embedding vector({embedder.dim}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        conn.execute(
            f"INSERT INTO {table}"
            " (id, collection, kind, content, embedding, created_at)"
            " VALUES (%s, 'legacy', 'knowledge', 'Old billing note',"
            " %s, now())",
            (str(uuid.uuid4()), "[1,0,0,0,0.1]"),
        )
    finally:
        conn.close()

    store = PgVectorSubstrate(
        postgres_url, collection="legacy", embedder=embedder, table=table
    )
    await store.start()
    try:
        entries = await store.recent(limit=5)
        assert entries and entries[0].source == "user"
        assert entries[0].author == "unknown"
    finally:
        await store.close()


async def test_table_name_is_quoted_not_interpolated(postgres_url):
    """The table name is composed as an identifier. A hostile one must become
    an oddly named table, not executable SQL."""
    sentinel = f"sentinel_{uuid.uuid4().hex[:8]}"
    conn = psycopg.connect(postgres_url, autocommit=True)
    try:
        conn.execute(f"CREATE TABLE {sentinel} (id int)")
        store = PgVectorSubstrate(
            postgres_url,
            collection="c",
            embedder=FakeEmbedder(),
            table=f'evil"; DROP TABLE {sentinel}; --',
        )
        await store.start()
        try:
            await store.record("knowledge", "billing note", source="user", author="t")
            assert (await store.retrieve("billing", limit=1))[
                0
            ].content == "billing note"
            assert (await store.recent(limit=1))[0].content == "billing note"
        finally:
            await store.close()
        # The "injection" did not run: the sentinel table is still there.
        assert conn.execute(f"SELECT count(*) FROM {sentinel}").fetchone() == (0,)
    finally:
        conn.close()
