from functools import lru_cache

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from temporalio.contrib.opentelemetry import create_tracer_provider

from agent_chassis.config import get_settings
from agent_chassis.utils.logger import get_logger

_logger = get_logger(__name__)


@lru_cache(maxsize=1)
def setup_tracing(service_name: str = "agent-chassis") -> bool:
    """Configure OTLP tracing (Phoenix-compatible). Fail-soft: unset
    PHOENIX_COLLECTOR_ENDPOINT = tracing disabled, set = export, never fail
    hard. In a cluster the deployment injects each tenant's own Phoenix
    collector endpoint."""
    settings = get_settings()
    # Temporal's replay-safe provider (required by OpenTelemetryInterceptor):
    # span IDs stay deterministic across workflow replays so re-executed
    # workflow code doesn't emit duplicate spans. Outside workflows it
    # behaves like a plain TracerProvider. It is installed even with no
    # endpoint — the worker's interceptor demands it at construction time;
    # without an exporter the spans simply go nowhere.
    provider = create_tracer_provider(
        resource=Resource.create(
            {
                "service.name": service_name,
                # Phoenix groups traces into projects by this attribute —
                # plain service.name lands everything in "default".
                "openinference.project.name": service_name,
            }
        )
    )
    trace.set_tracer_provider(provider)
    if not settings.phoenix_collector_endpoint:
        _logger.info("tracing disabled — PHOENIX_COLLECTOR_ENDPOINT not set")
        return False
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.phoenix_collector_endpoint)
        )
    )
    _logger.info("tracing enabled → %s", settings.phoenix_collector_endpoint)
    return True
