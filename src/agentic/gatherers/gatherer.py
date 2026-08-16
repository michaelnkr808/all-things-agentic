"""Filesystem gatherer (Malik).

Reference implementation for the gather() seam that Michael owns in
gather.py: list a department's files, filter loosely by relevance
(over-gather, never under-gather), enforce permissions on every path,
read the survivors into GatherResult.

Standalone and offline — works straight against data/departments/.
"""

from __future__ import annotations

from pathlib import Path

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatheredFile, GatherRequest, GatherResult
from agentic.gatherers import permissions


def _keyword_hits(path: Path, request: GatherRequest) -> int:
    """Loose relevance score: how many query terms appear in the filename."""
    haystack = f"{path.name} {path.stem}".lower()
    terms = [t for t in (request.keywords + request.query.split()) if t]
    return sum(1 for t in terms if t.lower() in haystack)


async def gather_files(request: GatherRequest, config: EnvironmentConfig) -> GatherResult:
    dept = config.department(request.department)
    root = Path(dept.path)
    result = GatherResult(request_id=request.request_id, department=request.department)

    candidates = []
    for pattern in dept.file_globs:
        candidates.extend(p for p in root.glob(pattern) if p.is_file())

    # Permission pre-filter first: anything denied here is never read.
    # The authoritative, non-bypassable gate is spawn.spawn_gatherers.
    allowed: list[Path] = []
    for p in candidates:
        rel = p.relative_to(root).as_posix()
        if permissions.check(rel, request.requester_role, dept):
            allowed.append(p)
        else:
            result.denied.append(rel)

    # Loose relevance ranking — over-gather bias via overgather_factor.
    ranked = sorted(allowed, key=lambda p: _keyword_hits(p, request), reverse=True)
    limit = int(request.max_files * config.gatherers.overgather_factor)
    limit = min(limit, config.gatherers.max_files_per_gatherer)
    for p in ranked[:limit]:
        rel = p.relative_to(root).as_posix()
        note = f"matched {_keyword_hits(p, request)} keyword(s)"
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # one bad file shouldn't sink the department
            result.errors.append(f"{rel}: {exc}")
            continue
        result.files.append(
            GatheredFile(
                path=rel,
                department=request.department,
                content=content,
                relevance_note=note,
            )
        )

    return result
