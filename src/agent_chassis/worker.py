"""Worker composition root.

The one place outside ``adapters/`` that may *reference* the vendor adapter
package (it must choose an adapter to compose the runtime); it still never
imports the vendor SDK directly. Everything else it wires is chassis code.

Run as: ``python -m agent_chassis.worker``
"""

import asyncio
import signal

from temporalio.client import Client
from temporalio.contrib.opentelemetry import OpenTelemetryInterceptor
from temporalio.worker import Worker

from agent_chassis.adapters.pydantic_ai import (
    PydanticAIAdapter,
    PydanticAIHandle,
    bind_workflow_agents,
    temporal_plugins,
)
from agent_chassis.channels.slack import SlackOutboundSender
from agent_chassis.config import ChassisSettings, get_settings
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.converter import build_data_converter
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.durable.schedules import ensure_schedules
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.memory.pgvector import PgVectorSubstrate
from agent_chassis.observability import setup_tracing
from agent_chassis.manifest import load_manifest
from agent_chassis.role_loader import build_substrate, load_role
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


def build_runtime(settings: ChassisSettings) -> RoleRuntime:
    resolved = load_role(settings)
    manifest = resolved.manifest
    for override in resolved.overrides:
        _logger.info(
            "manifest_override role=%s kind=%s pack=%s %s=%r (default %r)",
            override.role,
            override.kind,
            override.pack,
            override.setting,
            override.value,
            override.default,
        )
    adapter = PydanticAIAdapter()
    push_senders = {}
    if settings.slack_webhook_url and "slack" in manifest.channels:
        push_senders["slack"] = SlackOutboundSender(settings.slack_webhook_url)

    substrate = build_substrate(settings, manifest)
    return RoleRuntime(
        resolved=resolved,
        adapter=adapter,
        handle=adapter.build(resolved),
        push_senders=push_senders,
        substrate=substrate,
    )


async def run_worker(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    # Tracing first so instrumentation created during build_runtime binds to
    # the real provider, and the project carries the role name. Always called:
    # the interceptor below requires the replay-safe provider it installs.
    setup_tracing(
        service_name=(
            load_manifest(settings.manifest_path).employee.name
            if settings.manifest_path
            else "agent-chassis"
        )
    )
    runtime = build_runtime(settings)
    try:
        # Inside the try: a failure DURING pool startup must still reach the
        # finally, or its half-open background tasks block asyncio.run's
        # cleanup and the process hangs as a zombie instead of crash-looping.
        if isinstance(runtime.substrate, PgVectorSubstrate):
            await runtime.substrate.start()
        set_role_runtime(runtime)

        handle = runtime.handle
        assert isinstance(handle, PydanticAIHandle)
        # Agents bind to ONE workflow class only: PydanticAIPlugin registers
        # each bound agent's activities per class, and both workflows share
        # the worker — binding twice would register duplicate activities.
        bind_workflow_agents(ConversationWorkflow, handle)

        client = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            data_converter=build_data_converter(settings),
            plugins=temporal_plugins(),
            # Context propagation only — no Start*/Run* plumbing spans (they
            # drowned the real agent/llm/tool spans in Phoenix).
            interceptors=[OpenTelemetryInterceptor()],
        )
        activities = ChassisActivities(runtime)
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[ConversationWorkflow, TaskWorkflow],
            activities=activities.all,
        )
        await ensure_schedules(
            client,
            runtime.resolved.manifest,
            settings.temporal_task_queue,
            settings.tenant,
        )
        _logger.info(
            "worker started role=%s address=%s namespace=%s task_queue=%s",
            runtime.resolved.manifest.employee.name,
            settings.temporal_address,
            settings.temporal_namespace,
            settings.temporal_task_queue,
        )
        async with worker:
            await shutdown.wait()
    finally:
        # Without this, a startup failure leaves the pool's background tasks
        # blocking asyncio.run's cleanup — the process hangs as a zombie
        # instead of crash-looping with a traceback.
        if isinstance(runtime.substrate, PgVectorSubstrate):
            await runtime.substrate.close()


def main() -> None:  # pragma: no cover - process entrypoint
    shutdown = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown.set)
        await run_worker(shutdown)

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    main()
