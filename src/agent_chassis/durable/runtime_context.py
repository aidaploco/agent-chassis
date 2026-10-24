"""Process-level role runtime: one worker hosts exactly one role (D16).

Set once at worker startup, before any workflow task runs; identical across
replays of the same deployment, so workflow code may read it. The workflow
module passes this module through the sandbox for exactly that reason.
"""

from dataclasses import dataclass, field

from agent_chassis.channels.base import OutboundSender
from agent_chassis.contracts.runtime import RuntimeAdapter, RuntimeHandle
from agent_chassis.memory.base import MemorySubstrate
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
