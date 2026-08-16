"""Filesystem + cloud gatherer (Malik).

Reference implementation for the gather() seam that Michael owns in
gather.py: list a department's files (local dir or cloud backend),
filter loosely by relevance (over-gather, never under-gather), enforce
permissions on every path, read the survivors into GatherResult.

Standalone and offline for local departments — works straight against
data/departments/. Cloud departments (gcs / drive) go through the same
permission checks; only the read layer differs.
"""

from __future__ import annotations

from pathlib import Path

from agentic.contracts.config import DepartmentConfig, EnvironmentConfig, StorageConfig
from agentic.contracts.messages import GatheredFile, GatherRequest, GatherResult
from agentic.gatherers import permissions

from agentic.gatherers.cloud import Candidate


def _keyword_hits(path: Path, request: GatherRequest) -> int:
    """Loose relevance score: how many query terms appear in the filename."""
    haystack = f"{path.name} {path.stem}".lower()
    terms = [t for t in (request.keywords + request.query.split()) if t]
    return sum(1 for t in terms if t.lower() in haystack)


def _cap(request: GatherRequest, config: EnvironmentConfig) -> int:
    """Hard ceiling: max_files * overgather_factor, never over the per-gatherer cap."""
    limit = int(request.max_files * config.gatherers.overgather_factor)
    return min(limit, config.gatherers.max_files_per_gatherer)


def _gather_local(request: GatherRequest, config: EnvironmentConfig) -> GatherResult:
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
    for p in ranked[: _cap(request, config)]:
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


def _gather_cloud(request: GatherRequest, config: EnvironmentConfig, dept: DepartmentConfig) -> GatherResult:
    from agentic.gatherers.cloud import drive, gcs

    storage: StorageConfig = dept.storage  # type: ignore[assignment]
    result = GatherResult(request_id=request.request_id, department=request.department)
    backend = gcs if storage.provider == "gcs" else drive

    try:
        candidates: list[Candidate] = backend.list_candidates(storage)
    except Exception as exc:
        result.errors.append(f"cloud list failed: {exc}")
        return result

    allowed: list[Candidate] = []
    for c in candidates:
        if permissions.check(c.path, request.requester_role, dept):
            allowed.append(c)
        else:
            result.denied.append(c.path)

    ranked = sorted(allowed, key=lambda c: _keyword_hits(Path(c.path), request), reverse=True)
    for c in ranked[: _cap(request, config)]:
        note = f"matched {_keyword_hits(Path(c.path), request)} keyword(s)"
        if c.size is not None and c.size > storage.max_bytes:
            result.errors.append(f"{c.path}: exceeds max_bytes ({storage.max_bytes})")
            continue
        try:
            content = backend.download(storage, c.key)
        except Exception as exc:
            result.errors.append(f"{c.path}: {exc}")
            continue
        result.files.append(
            GatheredFile(
                path=c.path,
                department=request.department,
                content=content,
                relevance_note=note,
            )
        )

    return result


async def gather_files(request: GatherRequest, config: EnvironmentConfig) -> GatherResult:
    dept = config.department(request.department)
    if dept.storage is not None:
        return _gather_cloud(request, config, dept)
    return _gather_local(request, config)
