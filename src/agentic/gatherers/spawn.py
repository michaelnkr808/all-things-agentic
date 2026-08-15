"""Gatherer fan-out (Malik).

Build GatherRequests from the state manager's department picks, run
gather.gather(...) concurrently (respect config.gatherers.max_gatherers),
collect GatherResults.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult


async def spawn_gatherers(requests: list[GatherRequest], config: EnvironmentConfig) -> list[GatherResult]:
    """(Malik — TODO)"""
    raise NotImplementedError("Malik: spin up gatherers")
