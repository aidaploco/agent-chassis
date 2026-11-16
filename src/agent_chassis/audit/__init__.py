"""The audit stream (D11): who decided what, when.

One append-only record of approval decisions, escalations and the manifest
overrides in force. Everything here is vendor-free; the Postgres store is the
only implementation, and a deployment without a database falls back to logging.
"""

from agent_chassis.audit.base import AuditEvent, AuditSink
from agent_chassis.audit.overrides import manifest_digest, record_overrides
from agent_chassis.audit.pgaudit import PgAuditSink

__all__ = [
    "AuditEvent",
    "AuditSink",
    "PgAuditSink",
    "manifest_digest",
    "record_overrides",
]
