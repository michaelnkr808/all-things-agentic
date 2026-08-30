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

Two questions are asked of every returned file, not one: may this role read
this path in this department (policy), and is this path a file the department
actually holds (provenance). Policy alone would keep an invented path that
merely stays inside the root — see _is_department_file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from agentic import audit
from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult
from agentic.gatherers import gather, permissions


def _is_department_file(path: str, department) -> bool:
    """True if `path` is a file the department actually holds.

    Containment answers a different question than provenance, and only the
    first was being asked. `hr/comp-bands.md` returned under
    department="engineering" *is* contained: it names a subdirectory of
    engineering that happens not to exist, so nothing about it escapes the
    root and the path check passes it. A gatherer under prompt injection can
    therefore hand back an invented path with invented content and have it
    kept, cited, and rendered as a source the user was never cleared to see —
    not a leak of real HR data, which the gatherer never had, but a fabricated
    citation wearing a real department's name.

    Requiring the file to exist closes it: a gatherer can only return what its
    department actually holds. Files that reached here through _gate were
    globbed off disk and always pass; only invented paths are affected.

    Cloud-backed departments have no local file to stat. Their containment is
    enforced inside the adapter at download time, against the bucket prefix or
    folder parent-chain, so the equivalent check has already happened there.
    """
    if department.storage is not None:
        return True
    return (Path(department.path) / path).is_file()


def _enforce(
    request: GatherRequest,
    result: GatherResult,
    config: EnvironmentConfig,
    permissions_cfg: permissions.PermissionsConfig,
    role: str,
) -> None:
    """Strip any file the config-sourced role may not read, into result.denied."""
    dept = config.department(request.department)
    def deny(path: str, reason: str) -> None:
        result.denied.append(path)
        audit.record(
            audit.DENY,
            department=request.department,
            path=path,
            stage="spawn-gate",
            reason=reason,
        )

    survivors = []
    for f in result.files:
        if f.department != request.department:
            deny(f.path, f"gatherer labelled it {f.department!r}, not its own department")
            continue
        if request.requester_role != role:
            deny(f.path, f"request claimed role {request.requester_role!r}, trusted role is {role!r}")
            continue
        if not permissions.allowed(f.path, role, dept, permissions_cfg):
            deny(f.path, "role may not read this path in this department")
            continue
        if not _is_department_file(f.path, dept):
            # Passed policy, failed provenance — see _is_department_file.
            deny(f.path, "no such file in this department")
            continue
        survivors.append(f)
        audit.record(
            audit.ALLOW, department=request.department, path=f.path, stage="spawn-gate"
        )
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
