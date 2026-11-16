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
from agent_chassis.audit import PgAuditSink, manifest_digest, record_overrides
from agent_chassis.channels.base import OutboundSender
from agent_chassis.channels.slack import SlackOutboundSender
from agent_chassis.config import ChassisSettings, get_settings
from agent_chassis.durable.activities import ChassisActivities
from agent_chassis.durable.converter import build_data_converter
from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.durable.schedules import ensure_schedules
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.manifest import RoleManifest, load_manifest
from agent_chassis.memory.pgvector import PgVectorSubstrate
from agent_chassis.observability import setup_tracing
from agent_chassis.role_loader import build_substrate, load_role
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


def build_approvers_sender(
    settings: ChassisSettings, manifest: RoleManifest
) -> OutboundSender | None:
    """Where escalation and approval notices are pushed (D10), or ``None``.

    ``None`` is a legitimate configuration (an ``http`` approvers channel is
    pull-based: humans poll ``GET /v1/tasks/{id}``), but it means nobody is
    told. That is easy to ship by accident, so it is logged at start rather
    than discovered when an escalation goes unanswered.
    """
    channel = manifest.escalation.approvers_channel
    if channel == "slack":
        if settings.slack_approvers_webhook_url:
            # Its own webhook, and no channel derived from a conversation: the
            # notice carries internal reasons and tool arguments and must not
            # be able to land where a customer reads.
            return SlackOutboundSender(
                settings.slack_approvers_webhook_url,
                derive_channel=False,
                channel=settings.slack_approvers_channel,
            )
        _logger.warning(
            "escalation.approvers_channel is 'slack' but SLACK_APPROVERS_WEBHOOK_URL"
            " is not set — escalations and approval requests are only logged;"
            " nobody will be told"
        )
        return None
    _logger.warning(
        "escalation.approvers_channel is %r, which has no push path (http is"
        " pull-based) — escalations and approval requests are logged only;"
        " approvers must poll GET /v1/tasks/{id}. Set approvers_channel to"
        " 'slack' with SLACK_APPROVERS_WEBHOOK_URL to have them pushed",
        channel,
    )
    return None


def build_audit_sink(settings: ChassisSettings) -> PgAuditSink | None:
    """The audit stream's store (D11), or ``None`` without a database.

    ``None`` keeps today's behaviour (approvals, escalations and overrides are
    logged only) but it means there is no durable, append-only record, so it is
    said out loud at start rather than found when someone asks who approved what.
    """
    if not settings.postgres_url:
        _logger.warning(
            "POSTGRES_URL is not set — approvals, escalations and manifest"
            " overrides are only logged; there is no durable audit record (D11)"
        )
        return None
    return PgAuditSink(settings.postgres_url)


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
    absent = resolved.missing_secrets()
    if absent:
        raise RuntimeError(
            "capability packs require secrets that are not set in the"
            f" environment: {absent} — the worker will not start with tools"
            " that are certain to fail"
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
        approvers_sender=build_approvers_sender(settings, manifest),
        audit=build_audit_sink(settings),
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
        if isinstance(runtime.audit, PgAuditSink):
            await runtime.audit.start()
            # The overrides in force, once per manifest version (D11).
            assert settings.manifest_path is not None  # load_role required it
            recorded = await record_overrides(
                runtime.audit,
                runtime.resolved,
                tenant=settings.tenant,
                manifest_sha256=manifest_digest(settings.manifest_path),
            )
            _logger.info("audit: %d manifest override(s) newly recorded", recorded)
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
        if isinstance(runtime.audit, PgAuditSink):
            await runtime.audit.close()


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
