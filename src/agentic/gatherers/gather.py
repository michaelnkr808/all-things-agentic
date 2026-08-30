"""Gathering logic (Michael).

One cheap Gemini Flash agent per department. Its job is to READ — the whole
file, not a preview — and report what in it bears on the request. That is
what makes a file named ``budget_2026_final.csv`` findable from a question
about Q3 spend, and it is why ``relevance_note`` says something true about
the contents instead of guessing from a filename.

Two paths, one model call either way:

* The department fits under the budget (the common case): read every allowed
  file and assess them together. Nothing is filtered before reading, so
  nothing can be missed.
* The department is too large to read wholesale: build a manifest of names,
  sizes and openings, have the model select from it, then read and assess
  the survivors. Selection only exists because the budget is finite.

Bias LOOSE throughout: over-gather rather than under-gather. If the model
finds nothing relevant, everything read is returned rather than nothing —
a gap downstream is worse than noise.

Permissions are gated BEFORE anything is read or previewed. spawn.py
re-verifies every returned file anyway, but a file the principal may not
read must never reach a prompt sent to a model provider; stripping it
afterwards would be too late.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

from google import genai
from google.genai import types
from pydantic import BaseModel

from agentic.contracts.config import DepartmentConfig, EnvironmentConfig
from agentic.contracts.messages import GatheredFile, GatherRequest, GatherResult
from agentic import audit
from agentic.gatherers import permissions
from agentic.retry import with_retry

# Total characters a department may hand the model in one read-everything
# pass. Above this, selection runs first.
READ_ALL_CHARS = 200_000

# Per-file cap on text sent to the model. The full text is still stored on
# the GatheredFile — this only bounds the prompt.
MAX_ASSESS_CHARS = 40_000

# How much of a file the manifest shows during selection.
PREVIEW_CHARS = 400

SELECT_INSTRUCTION = """\
You are a resource gatherer for one department of an enterprise document \
system. This department holds more material than can be read in full, so you \
are given a manifest: each file's path, size, and the opening of its text.

Choose every file that might bear on the request.

Bias toward including. A file you include but that turns out to be irrelevant \
costs one cheap read. A file you leave out that mattered is a wrong answer \
nobody can trace back. Include anything plausibly related — same topic, same \
period, same team, or likely to contain a figure the request asks about.

An opening is not the whole file. A file whose first lines are a title page or \
boilerplate may still hold exactly what the request needs. Judge on what the \
file is likely to contain, not only on what the opening happens to show.

Return only paths that appear verbatim in the manifest.\
"""

ASSESS_INSTRUCTION = """\
You are a resource gatherer for one department of an enterprise document \
system. You are given a request and the full text of the department's files. \
For each file, decide whether anything in it bears on the request, and say \
what.

Mark a file relevant if it contains anything that could inform an answer — a \
figure, a date, a decision, a constraint, context that frames the question. \
Partial relevance counts. A file holding one useful number among ten \
irrelevant ones is relevant.

Mark a file irrelevant only when it is plainly about something else.

In `note`, name what specifically connects the file to the request — quote or \
paraphrase the part that matters. "Contains budget data" is not useful; \
"line 2 gives engineering infra actual spend of $42,000 against $50,000 \
budgeted" is. This note is shown to a reviewer downstream, so it must be true \
of the file's actual contents.

Return one entry per file you were given, using the paths exactly as provided.\
"""


class _Pick(BaseModel):
    path: str
    reason: str


class _Picks(BaseModel):
    picks: list[_Pick]


class _Assessment(BaseModel):
    path: str
    relevant: bool
    note: str


class _Assessments(BaseModel):
    assessments: list[_Assessment]


#: on_progress(event_name, payload) — optional live narration of one gatherer's
#: work, for the streaming frontend. Advisory only: the default is None, the CLI
#: path is unchanged, and nothing here may mutate the GatherResult. The callback
#: is invoked on the event loop thread and must not raise or block.
#:
#: Events: scanning {candidates} · file_denied {path} · selecting {count} ·
#: file_read {path, bytes} · assessing {count} · file_assessed {path, relevant, note}
#: Every payload also carries `department`.
Progress = Callable[[str, dict], None]


def _narrator(request: GatherRequest, on_progress: Progress | None):
    """Bind the department to a progress callback, or return a no-op."""
    if on_progress is None:
        return lambda event, **fields: None

    def narrate(event: str, **fields) -> None:
        on_progress(event, {"department": request.department, **fields})

    return narrate


@lru_cache(maxsize=1)
def _default_permissions() -> permissions.PermissionsConfig:
    """Loaded once — gatherers run concurrently and would otherwise each read it."""
    return permissions.load_permissions_config()


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client()


def _model_for(request: GatherRequest, config: EnvironmentConfig) -> str:
    """agent_types is authoritative (it is what plan_to_requests reads)."""
    try:
        return config.agent_types[request.agent_type].model
    except KeyError:
        return config.models.gatherer


def _limit(request: GatherRequest, config: EnvironmentConfig) -> int:
    """Overgather is advisory; max_files_per_gatherer is a hard ceiling."""
    wanted = int(request.max_files * config.gatherers.overgather_factor)
    return max(1, min(wanted, config.gatherers.max_files_per_gatherer))


def _gate(
    request: GatherRequest,
    config: EnvironmentConfig,
    perms: permissions.PermissionsConfig,
    result: GatherResult,
    narrate=None,
) -> dict[str, Path]:
    """Glob the department, gate every path, return {relative path: full path}.

    Denials land in result.denied. Nothing here opens a file.
    """
    narrate = narrate or (lambda event, **fields: None)
    dept = config.department(request.department)
    root = Path(dept.path)

    candidates: list[Path] = []
    for pattern in dept.file_globs:
        candidates.extend(p for p in root.glob(pattern) if p.is_file())

    candidates = sorted(set(candidates))
    narrate("scanning", candidates=len(candidates))

    allowed: dict[str, Path] = {}
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if permissions.allowed(rel, perms.principal.role, dept, perms):
            allowed[rel] = path
            audit.record(
                audit.ALLOW, department=dept.name, path=rel, stage="pre-read"
            )
        else:
            result.denied.append(rel)
            audit.record(
                audit.DENY,
                department=dept.name,
                path=rel,
                stage="pre-read",
                reason="role may not read this path in this department",
            )
            # Denials are narrated as they happen, before a single file is
            # opened — the locked nodes appear first, which is the point.
            narrate("file_denied", path=rel)
    return allowed


def _total_size(allowed: dict[str, Path]) -> int:
    total = 0
    for path in allowed.values():
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _read(paths: dict[str, Path], result: GatherResult, narrate=None) -> dict[str, str]:
    """Read each file; one bad file records an error instead of sinking the rest."""
    narrate = narrate or (lambda event, **fields: None)
    documents: dict[str, str] = {}
    for rel, path in paths.items():
        try:
            documents[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.errors.append(f"{rel}: {exc}")
            continue
        narrate("file_read", path=rel, bytes=len(documents[rel]))
    return documents


def _request_header(request: GatherRequest) -> str:
    return (
        f"REQUEST:\n{request.query}\n\n"
        f"KEYWORD HINTS: {', '.join(request.keywords) or '(none)'}\n\n"
        f"DEPARTMENT: {request.department}\n\n"
    )


async def _generate(instruction: str, message: str, model: str, schema: type):
    """One structured Gemini call, retried through the shared policy."""

    async def once():
        response = await _client().aio.models.generate_content(
            model=model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if response.parsed is None:
            raise ValueError("gatherer model returned no parsable response")
        return response.parsed

    return await with_retry(once, what="gatherer")


async def _select(
    allowed: dict[str, Path],
    request: GatherRequest,
    config: EnvironmentConfig,
    result: GatherResult,
) -> dict[str, Path]:
    """Narrow an oversized department to files worth reading, via a manifest."""
    lines = []
    for rel, path in allowed.items():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                preview = f.read(PREVIEW_CHARS)
        except OSError as exc:
            result.errors.append(f"{rel}: preview failed: {exc}")
            continue
        flat = " ".join(preview.split())
        lines.append(f"- {rel} ({path.stat().st_size} bytes): {flat}")

    picks = await _generate(
        SELECT_INSTRUCTION,
        _request_header(request) + "AVAILABLE FILES:\n" + "\n".join(lines),
        _model_for(request, config),
        _Picks,
    )

    selected: dict[str, Path] = {}
    for pick in picks.picks[: _limit(request, config)]:
        # The model may only choose from the manifest. An invented path must
        # never cause a read, and must never bypass the gate above.
        if pick.path in allowed:
            selected[pick.path] = allowed[pick.path]
        else:
            result.errors.append(f"model proposed an unlisted path: {pick.path!r}")
    return selected


async def _assess(
    documents: dict[str, str], request: GatherRequest, config: EnvironmentConfig
) -> dict[str, _Assessment]:
    """Full-text read: which files bear on the request, and how."""
    blocks = []
    for rel, content in documents.items():
        text = content
        if len(text) > MAX_ASSESS_CHARS:
            text = text[:MAX_ASSESS_CHARS] + "\n[... truncated ...]"
        blocks.append(f"--- FILE: {rel} ---\n{text}")

    assessed = await _generate(
        ASSESS_INSTRUCTION,
        _request_header(request) + "FILES:\n\n" + "\n\n".join(blocks),
        _model_for(request, config),
        _Assessments,
    )
    return {a.path: a for a in assessed.assessments if a.path in documents}


async def _assess_and_collect(
    documents: dict[str, str],
    request: GatherRequest,
    config: EnvironmentConfig,
    result: GatherResult,
    narrate=None,
) -> GatherResult:
    """Assess already-read documents and collect the keepers onto `result`.

    Shared by the local and cloud paths so a cloud file carries the same
    grounded relevance_note a local one does.
    """
    narrate = narrate or (lambda event, **fields: None)
    narrate("assessing", count=len(documents))
    try:
        assessments = await _assess(documents, request, config)
        for rel, a in assessments.items():
            narrate("file_assessed", path=rel, relevant=a.relevant, note=a.note)
    except Exception as exc:
        # The files are already read. Returning them unassessed keeps the
        # department in the answer; dropping them would be a silent gap.
        result.errors.append(
            f"assessment model unavailable ({exc}); returned unassessed"
        )
        assessments = {}

    relevant = {rel: a for rel, a in assessments.items() if a.relevant}
    if not relevant:
        # Nothing was judged relevant (or assessment failed). Hand back what
        # was read rather than an empty department — noise beats a gap.
        # Narrated so the frontend doesn't leave these files greyed out as
        # "not relevant" when they are in fact about to be used.
        narrate("kept_unassessed", count=len(documents))
        for rel, content in documents.items():
            note = assessments[rel].note if rel in assessments else ""
            result.files.append(
                GatheredFile(
                    path=rel,
                    department=request.department,
                    content=content,
                    relevance_note=note
                    or "returned unassessed; no file judged relevant",
                )
            )
        return result

    for rel, assessment in relevant.items():
        result.files.append(
            GatheredFile(
                path=rel,
                department=request.department,
                content=documents[rel],
                relevance_note=assessment.note,
            )
        )
    return result


async def _gather_cloud(
    request: GatherRequest,
    config: EnvironmentConfig,
    dept: DepartmentConfig,
    perms: permissions.PermissionsConfig,
    narrate=None,
) -> GatherResult:
    """GCS/Drive departments: Malik's adapter does the I/O, we do the assessment.

    gatherer.gather_files owns listing, download, size caps, and backend error
    handling — no reason to reimplement any of it. What it does not do is our
    assessment pass, so its keyword-rank notes get replaced here and a cloud
    file ends up indistinguishable from a local one downstream.
    """
    from agentic.gatherers import gatherer

    narrate = narrate or (lambda event, **fields: None)
    narrate("downloading", provider=dept.storage.provider if dept.storage else None)

    result = await gatherer.gather_files(request, config)

    # That path gates with permissions.check (environment config only); the
    # full gate also requires the permissions config to grant the department.
    # Re-apply it before any of this content reaches a model prompt.
    documents: dict[str, str] = {}
    for f in result.files:
        if permissions.allowed(f.path, perms.principal.role, dept, perms):
            documents[f.path] = f.content
            narrate("file_read", path=f.path, bytes=len(f.content))
        else:
            result.denied.append(f.path)
            narrate("file_denied", path=f.path)
    result.files = []

    if not documents:
        return result

    return await _assess_and_collect(documents, request, config, result, narrate)


async def gather(
    request: GatherRequest,
    config: EnvironmentConfig,
    permissions_cfg: permissions.PermissionsConfig | None = None,
    on_progress: Progress | None = None,
) -> GatherResult:
    """Read and assess the plausibly-relevant files for one department."""
    perms = permissions_cfg or _default_permissions()
    narrate = _narrator(request, on_progress)

    dept = config.department(request.department)
    if dept.storage is not None:
        return await _gather_cloud(request, config, dept, perms, narrate)

    result = GatherResult(request_id=request.request_id, department=request.department)

    allowed = _gate(request, config, perms, result, narrate)
    if not allowed:
        return result

    # Selection is a concession to a finite budget, not the default. When the
    # department fits, every file is read and nothing can be filtered away.
    if len(allowed) > _limit(request, config) or _total_size(allowed) > READ_ALL_CHARS:
        narrate("selecting", count=len(allowed))
        try:
            allowed = await _select(allowed, request, config, result)
        except Exception as exc:
            from agentic.gatherers import gatherer

            fallback = await gatherer.gather_files(request, config)
            fallback.errors.append(
                f"selection model unavailable ({exc}); used keyword match"
            )
            return fallback
        if not allowed:
            return result

    documents = _read(allowed, result, narrate)
    if not documents:
        return result

    return await _assess_and_collect(documents, request, config, result, narrate)
