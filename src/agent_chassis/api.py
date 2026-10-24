"""HTTP process entrypoint: ``uvicorn agent_chassis.api:app``.

Composes the enabled channel adapters over one lazy Temporal client and, when
a manifest is mounted, the introspection endpoint. Importing does no I/O.
"""

import asyncio

from agent_chassis.channels.http import create_app, default_client_provider
from agent_chassis.channels.slack import create_router
from agent_chassis.config import get_settings
from agent_chassis.memory.base import MemorySubstrate
from agent_chassis.memory.pgvector import PgVectorSubstrate
from agent_chassis.observability import setup_tracing
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
                        await substrate.start()
                    cache.append(substrate)
        return cache[0]

    return get_substrate


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
    application = create_app(get_client, resolved=resolved, get_substrate=get_substrate)
    application.include_router(create_router(get_client))
    return application


app = _build()
