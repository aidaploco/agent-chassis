"""Process-level role runtime: one worker hosts exactly one role (D16).

Set once at worker startup, before any workflow task runs; identical across
replays of the same deployment, so workflow code may read it. The workflow
module passes this module through the sandbox for exactly that reason.
"""

from dataclasses import dataclass, field

from temporalio.common import RetryPolicy

from agent_chassis.audit.base import AuditSink
from agent_chassis.channels.base import OutboundSender
from agent_chassis.contracts.runtime import RuntimeAdapter, RuntimeHandle
from agent_chassis.memory.base import MemorySubstrate
from agent_chassis.outcomes.base import OutcomeSink
from agent_chassis.packs.registry import ResolvedRole


@dataclass(frozen=True)
class RoleRuntime:
    resolved: ResolvedRole
    adapter: RuntimeAdapter
    handle: RuntimeHandle
    # Push channels with a registered worker-side sender (D15); channels
    # absent here (e.g. http) reply via the update result instead.
    push_senders: dict[str, OutboundSender] = field(default_factory=dict)
    # Bound memory substrate (D8); None when the role runs memory-less.
    substrate: MemorySubstrate | None = None
    # Delivers escalation / approval notices to escalation.approvers_channel
    # (D10). None when that channel has no push path (e.g. http, which is
    # pull-based): notices are then logged only.
    approvers_sender: OutboundSender | None = None
    # The audit stream (D11). None when no database is configured: approvals and
    # escalations are then only logged, and the worker warned about it at start.
    audit: AuditSink | None = None
    # The outcome feed (D12). None when no database is configured: outcomes are
    # then only logged.
    outcomes: OutcomeSink | None = None
    # Test hook (like ConversationParams.max_turns_per_run): replaces the retry
    # policy for the outcome write so a test of the give-up path need not wait
    # minutes. Production leaves it None.
    outcome_retry: RetryPolicy | None = None


_runtime: RoleRuntime | None = None


def set_role_runtime(runtime: RoleRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_role_runtime() -> RoleRuntime:
    if _runtime is None:
        raise RuntimeError(
            "RoleRuntime is not set — worker startup must call set_role_runtime"
        )
    return _runtime
