"""Append-only ledger of permission decisions (Michael).

The gates already decide correctly. What they did not do is leave a record,
and "we enforce this" is a claim while "here is the receipt" is evidence. An
enterprise fleet is audited on the second one.

Every decision the permission layer makes is written here as one JSON line:
which principal, which role, which department, which path, allowed or denied,
and which of the two gates decided it. Both gates are recorded on purpose —
seeing the same file pass the pre-read check and then pass the spawn gate
again is what makes the defence-in-depth visible rather than asserted.

Context, not plumbing
---------------------
A decision is made deep inside a gatherer that knows nothing about runs or
users, and threading run/principal through five signatures to reach it would
put audit concerns in every one of them. A ContextVar carries it instead:
the server opens a ``run_context`` around a run and every decision inside it,
including ones made in the tasks ``asyncio.gather`` spawns, picks the context
up automatically.

Outside a run context ``record`` does nothing. That is deliberate: the ledger
is a record of runs, so a unit test calling the gate directly writes no line
and the file stays meaningful.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path("out/audit.jsonl")

#: Set per run by the server (or any caller that wants a ledger). Empty means
#: "not inside a run" and record() is a no-op.
_context: ContextVar[dict] = ContextVar("audit_context", default={})

#: Appends happen from concurrent gatherer tasks on one loop thread, but the
#: file is also opened per write; the lock keeps a line from interleaving if
#: this ever runs under more than one thread.
_lock = threading.Lock()

ALLOW = "allow"
DENY = "deny"


def log_path() -> Path:
    return Path(os.environ.get("AGENTIC_AUDIT_LOG", str(DEFAULT_LOG)))


@contextmanager
def run_context(run_id: str, principal: str, role: str, prompt: str = ""):
    """Attach run identity to every permission decision made inside the block."""
    token = _context.set(
        {"run_id": run_id, "principal": principal, "role": role, "prompt": prompt}
    )
    try:
        yield
    finally:
        _context.reset(token)


def active() -> dict:
    return _context.get()


def record(
    decision: str,
    *,
    department: str,
    path: str,
    stage: str,
    reason: str = "",
) -> None:
    """Append one decision. Silent no-op outside a run context.

    Never raises: a ledger that can take down a run is worse than a gap in
    the ledger, and the decision it is recording has already been enforced.
    """
    context = _context.get()
    if not context:
        return

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": context.get("run_id", ""),
        "principal": context.get("principal", ""),
        "role": context.get("role", ""),
        "department": department,
        "path": path,
        "decision": decision,
        "stage": stage,
    }
    if reason:
        entry["reason"] = reason

    try:
        target = log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry) + "\n"
        with _lock, target.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def read(
    principal: str | None = None, limit: int = 200, path: Path | None = None
) -> list[dict]:
    """Recent entries, newest first, optionally only one principal's.

    ``principal`` is a filter the caller is expected to pin to the
    authenticated user: an audit line names files inside departments the
    reader may not be cleared for, so one user's ledger is not another's to
    browse.
    """
    target = path or log_path()
    if not target.is_file():
        return []

    entries: list[dict] = []
    try:
        with target.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line must not sink the whole read
                if principal is not None and entry.get("principal") != principal:
                    continue
                entries.append(entry)
    except OSError:
        return []

    return entries[-limit:][::-1]


def summarise(entries: list[dict]) -> dict:
    """Counts a UI can show without walking the list itself."""
    allowed = sum(1 for e in entries if e.get("decision") == ALLOW)
    denied = sum(1 for e in entries if e.get("decision") == DENY)
    return {
        "total": len(entries),
        "allowed": allowed,
        "denied": denied,
        "departments": sorted({e.get("department", "") for e in entries} - {""}),
        "runs": len({e.get("run_id", "") for e in entries} - {""}),
    }
