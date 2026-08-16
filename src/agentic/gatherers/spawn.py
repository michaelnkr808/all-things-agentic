"""Gatherer fan-out (Malik).

Build GatherRequests from the state manager's department picks, run
gather.gather(...) concurrently (respect config.gatherers.max_gatherers),
collect GatherResults.
"""

from __future__ import annotations

import asyncio

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult
from agentic.gatherers import gather


async def spawn_gatherers(requests: list[GatherRequest], config: EnvironmentConfig) -> list[GatherResult]:
    """Fan out one gatherer per request, capped by config.gatherers.max_gatherers."""
    semaphore = asyncio.Semaphore(config.gatherers.max_gatherers)

    async def _bounded(request: GatherRequest) -> GatherResult:
        async with semaphore:
            return await gather.gather(request, config)

    return await asyncio.gather(*[_bounded(r) for r in requests])
