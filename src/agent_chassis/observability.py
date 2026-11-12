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
    """Configure OTLP tracing (Phoenix- and Logfire-compatible). Fail-soft:
    unset OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = tracing disabled, set = export,
    never fail hard. In a cluster the deployment injects each tenant's own
    collector endpoint (and auth headers, if the backend needs them)."""
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
    if not settings.otlp_traces_endpoint:
        _logger.info("tracing disabled — OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not set")
        return False
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otlp_traces_endpoint,
                headers=_parse_headers(settings.otlp_headers),
            )
        )
    )
    # Log the endpoint only — headers may carry a write token.
    _logger.info("tracing enabled → %s", settings.otlp_traces_endpoint)
    return True


def _parse_headers(raw: str | None) -> dict[str, str] | None:
    """Parse OTLP-style ``k=v,k2=v2`` headers; None lets the exporter default."""
    if not raw:
        return None
    pairs = (item.partition("=") for item in raw.split(",") if item.strip())
    return {k.strip(): v.strip() for k, _, v in pairs if k.strip()} or None
