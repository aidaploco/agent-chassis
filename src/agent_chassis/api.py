"""HTTP process entrypoint: ``uvicorn agent_chassis.api:app``.

Composes the enabled channel adapters over one lazy Temporal client and, when
a manifest is mounted, the introspection endpoint. Importing reads the
manifest and loads the role's packs, but opens no network connection: the
Temporal client and the memory substrate connect lazily on first use.
"""

import asyncio

from agent_chassis.audit import PgAuditSink
from agent_chassis.channels.http import create_app, default_client_provider
from agent_chassis.channels.slack import create_router
from agent_chassis.config import get_settings
from agent_chassis.memory.base import MemorySubstrate
from agent_chassis.memory.pgvector import PgVectorSubstrate
from agent_chassis.observability import setup_tracing
from agent_chassis.outcomes import PgOutcomeSink
from agent_chassis.role_loader import build_substrate, load_role
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


def _substrate_provider(resolved):  # lazy start, cached
    lock = asyncio.Lock()
    cache: list[MemorySubstrate | None] = []

    async def get_substrate() -> MemorySubstrate | None:
        if not cache:
            async with lock:
                if not cache:
                    substrate = build_substrate(get_settings(), resolved.manifest)
                    if isinstance(substrate, PgVectorSubstrate):
                        try:
                            await substrate.start()
                        except Exception:
                            # Introspection must not fail on a sick substrate:
                            # serve without outcome history now, and retry the
                            # connection on the next request (nothing cached).
                            _logger.exception(
                                "memory substrate unavailable — /v1/self"
                                " serves without recent outcomes"
                            )
                            await substrate.close()
                            return None
                    cache.append(substrate)
        return cache[0]

    return get_substrate


def _store_provider(make):
    """A lazily opened, cached READ-ONLY store: the api never creates the tables (the
    worker owns the schema). ``None`` when POSTGRES_URL is unset or the database cannot
    be reached; nothing is cached on failure, so the next request retries."""
    lock = asyncio.Lock()
    cache: list = []

    async def get_store():
        if cache:
            return cache[0]
        dsn = get_settings().postgres_url
        if not dsn:
            return None
        async with lock:
            if not cache:
                store = make(dsn)
                try:
                    await store.open_for_reads()
                except Exception:
                    _logger.exception("could not open the store for reads")
                    await store.close()
                    return None
                cache.append(store)
        return cache[0]

    return get_store


def _build():  # pragma: no cover - deployment composition
    settings = get_settings()
    resolved = None
    get_substrate = None
    try:
        resolved = load_role(settings)
        get_substrate = _substrate_provider(resolved)
        setup_tracing(service_name=resolved.manifest.employee.name)
    except Exception:
        _logger.exception(
            "role could not be loaded — /v1/self disabled, channels still up"
        )
        setup_tracing()
    get_client = default_client_provider()
    application = create_app(
        get_client,
        resolved=resolved,
        get_substrate=get_substrate,
        get_audit=_store_provider(PgAuditSink),
        get_outcomes=_store_provider(PgOutcomeSink),
    )
    # The Slack webhook exists only for roles that enable the channel. If the
    # role could not be loaded we cannot tell, so the route stays up.
    if resolved is None or "slack" in resolved.manifest.channels:
        application.include_router(create_router(get_client))
    return application


app = _build()
