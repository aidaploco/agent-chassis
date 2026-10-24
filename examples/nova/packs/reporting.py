"""Reporting capability for Nova, the ops/reporting Employee.

Mock-backed metrics like Lana's billing mock: a real metrics backend swaps
these handlers only. ``publish_report`` is the destructive action — it is
approval-gated via the manifest's ``escalation.approval_required_tools``,
NOT in this code: the same pack composed into another role could run it
freely. Handlers are stateless (no shared module state across runs)."""

from agent_chassis.packs import CapabilityPack, PackConfig, ToolSpec


class ReportingPack(CapabilityPack):
    name = "reporting"
    description = "operational metrics, error summaries, and publishing reports"

    def instructions(self, config: PackConfig) -> str:
        return (
            "You are the operations reporter. Compile reports from your"
            " metrics tools — never invent numbers; every figure in a report"
            " must come verbatim from a tool.\n"
            "A report has a one-line headline verdict, the key numbers, and"
            " anything anomalous. Keep it under 150 words.\n"
            "When asked to publish, call publish_report with the finished"
            " report — publishing needs human approval, which the chassis"
            " handles; do not ask for permission yourself."
        )

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def get_delivery_metrics() -> str:
            """Last 24h webhook delivery metrics."""
            return (
                "events delivered: 4,812,331 | success rate: 99.94% |"
                " p50 latency: 38ms | p99 latency: 212ms |"
                " dead-lettered: 1,204 | retries exhausted: 88"
            )

        async def get_error_summary() -> str:
            """Last 24h error summary by category."""
            return (
                "5xx from customer endpoints: 2,113 (0.04%) |"
                " TLS handshake failures: 312 |"
                " signature verification rejects: 45 |"
                " top offender: endpoint api.example.com/hooks (1,432 errors)"
            )

        async def publish_report(title: str, body: str) -> str:
            """Publish a finished report to the ops feed. Destructive:
            published reports reach every stakeholder immediately."""
            return f"Report published to the ops feed: {title!r} ({len(body)} chars)"

        return [
            ToolSpec("get_delivery_metrics", get_delivery_metrics, "Delivery metrics"),
            ToolSpec("get_error_summary", get_error_summary, "Error summary"),
            ToolSpec(
                "publish_report",
                publish_report,
                "Publish a finished report to the ops feed. Call it once the"
                " report is composed — approval is handled by the chassis,"
                " never ask for permission in text.",
            ),
        ]


PACKS = [ReportingPack()]
