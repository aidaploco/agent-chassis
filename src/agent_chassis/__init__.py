"""Runtime chassis for role-based AI Employees."""

from importlib.metadata import version

# Single source of truth is pyproject.toml (this constant had drifted from it).
__version__ = version("agent-chassis")
