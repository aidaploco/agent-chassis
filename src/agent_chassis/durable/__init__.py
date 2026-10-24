"""Durable execution chassis (D1, D3, D6, D13).

Everything in this package is vendor-free: the workflow shell owns the
deterministic orchestration (turn queue, routing loop, guardrail pre-flight,
idle timeout, continue-as-new) and reaches the agent runtime only through the
RuntimeAdapter seam, via the process-level RoleRuntime context.
"""
