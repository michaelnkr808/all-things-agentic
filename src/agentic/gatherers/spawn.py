"""Gatherer fan-out (Malik).

Build GatherRequests from the state manager's department picks, run
gather.gather(...) concurrently (respect config.gatherers.max_gatherers),
collect GatherResults.

spawn_gatherers is the mandatory, non-bypassable permission gate: every
file a gatherer returns is re-verified against the permissions config
before it can reach synthesis/output. However a gatherer is implemented,
and whatever it claims to have gathered, it cannot get a file past this
gate unless the config-sourced principal role may read it in that
department. The config principal role overrides whatever role a request
claims; a request that disagrees is denied entirely (fail closed).
"""

from __future__ import annotations

import asyncio
from typing import Callable

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult
from agentic.gatherers import gather, permissions


def _enforce(
    request: GatherRequest,
    result: GatherResult,
    config: EnvironmentConfig,
    permissions_cfg: permissions.PermissionsConfig,
    role: str,
) -> None:
    """Strip any file the config-sourced role may not read, into result.denied."""
    dept = config.department(request.department)
    survivors = []
    for f in result.files:
        if f.department != request.department:
            result.denied.append(f.path)
            continue
        if request.requester_role != role:
            result.denied.append(f.path)
            continue
        if permissions.allowed(f.path, role, dept, permissions_cfg):
            survivors.append(f)
        else:
            result.denied.append(f.path)
    result.files = survivors


async def spawn_gatherers(
    requests: list[GatherRequest],
    config: EnvironmentConfig,
    permissions_cfg: permissions.PermissionsConfig | None = None,
    on_result: Callable[[GatherRequest, GatherResult], None] | None = None,
    on_progress: Callable[[str, dict], None] | None = None,
) -> list[GatherResult]:
    """Fan out one gatherer per request, capped by config.gatherers.max_gatherers.

    ``on_result`` (optional, sync) fires after the gate has stripped each
    result, so observers (server SSE — provisional, Malik) see the final
    kept/denied counts. It must not mutate the result.

    ``on_progress`` (optional, sync) is handed to each gatherer for live
    per-file narration while it works — see gather.Progress. Interleaved
    across the fan-out, which is the point: each payload names its
    department.
    """
    perms = permissions_cfg or permissions.load_permissions_config()
    role = perms.principal.role

    semaphore = asyncio.Semaphore(config.gatherers.max_gatherers)

    async def _bounded(request: GatherRequest) -> GatherResult:
        async with semaphore:
            return await gather.gather(request, config, perms, on_progress)

    results = await asyncio.gather(*[_bounded(r) for r in requests])

    for request, result in zip(requests, results):
        _enforce(request, result, config, perms, role)
        if on_result is not None:
            on_result(request, result)

    return results
