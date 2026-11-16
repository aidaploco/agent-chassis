"""The outcome feed (D12): one row per finished conversation or task.

The raw feed for usage, cost and reliability metrics. Vendor-free; Postgres is
the only store, and without a database outcomes are only logged.
"""

from agent_chassis.outcomes.base import OutcomeSink
from agent_chassis.outcomes.pgoutcomes import PgOutcomeSink

__all__ = ["OutcomeSink", "PgOutcomeSink"]
