"""Recorded runs, replayed with no model calls (Michael).

A live run costs money, needs two API keys, and can die on a quota wall or
an empty credit balance halfway through a demo. A recording is the same run
frozen: the exact SSE frames the frontend already knows how to consume,
written to disk once and streamed back on demand. The page cannot tell the
difference, because there is no difference — same event names, same
payloads, same order, same pacing.

    AGENTIC_RECORD=1 uvicorn ...      # every run also writes recordings/<slug>.json
    scripts/record_demo.py "prompt"   # or record headlessly, no browser

Two things are deliberately not shortcuts:

- **A replay is pinned to the role that recorded it.** A run recorded as
  admin contains files an analyst is not allowed to read. Serving that
  recording to an analyst would walk straight past the four gates the rest
  of this project exists to enforce, so ``stream_recording`` is only ever
  reached after the caller's authenticated role matches ``role``. The
  listing shows which role each recording needs.
- **Names are slugs, never paths.** ``NAME_RE`` rejects anything with a
  separator or a dot before the filesystem is touched, and the resolved
  path is re-checked against the recordings directory.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

RECORDINGS_DIR = Path("recordings")

#: Saved graph pages live in a subdirectory, and *only* that subdirectory is
#: mounted for static serving. The .json recordings must never be reachable
#: without going through the role check in /api/replay, and a static mount
#: over the recordings directory itself would have served them wholesale.
PAGES_SUBDIR = "pages"

#: Recording names are slugs, not paths: no separators, no dots, no traversal.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

FORMAT_VERSION = 1

#: Live runs wait on models; a replay should not make a viewer wait the same
#: 9 seconds for a planner call. Long gaps collapse, short ones are honoured,
#: so the *shape* of the run survives without the dead air.
MAX_GAP = 1.5

MIN_SPEED, MAX_SPEED = 0.25, 8.0


class ReplayError(ValueError):
    """A recording that cannot be served (bad name, missing, unreadable)."""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug or "run"


def _safe_path(name: str, directory: Path, suffix: str) -> Path:
    """Resolve `name` inside `directory`, refusing anything that escapes it."""
    if not NAME_RE.match(name):
        raise ReplayError(f"invalid recording name {name!r}")
    # strict=False: the directory may not exist yet on the write path.
    directory = directory.resolve(strict=False)
    path = (directory / f"{name}{suffix}").resolve(strict=False)
    if path.parent != directory:
        raise ReplayError(f"invalid recording name {name!r}")
    return path


def page_path(name: str, directory: Path = RECORDINGS_DIR) -> Path:
    """Where a recording's standalone graph page is saved."""
    return _safe_path(name, directory / PAGES_SUBDIR, ".html")


def pages_dir(directory: Path = RECORDINGS_DIR) -> Path:
    return directory / PAGES_SUBDIR


# ------------------------------------------------------------------ record


@dataclass
class Recorder:
    """Wraps an ``emit`` callable, keeping a timestamped copy of every event.

    Timestamps are relative to the first event, so a recording replays at
    the pace it was captured at without depending on wall-clock time.
    """

    prompt: str
    role: str
    name: str
    events: list[dict] = field(default_factory=list)
    _t0: float | None = None

    def emit(self, event: str, payload: dict) -> None:
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = now
        self.events.append(
            {"t": round(now - self._t0, 3), "event": event, "payload": payload}
        )

    def wrap(self, emit):
        """Return an emit that records and then forwards."""

        def recording_emit(event: str, payload: dict) -> None:
            self.emit(event, payload)
            emit(event, payload)

        return recording_emit

    def save(self, directory: Path = RECORDINGS_DIR) -> Path:
        """Write the recording, plus a copy of the run's standalone graph page.

        The graph page lives under ``out/`` which is not committed, so a
        recording that only referenced it would arrive with a dead link on
        anyone else's machine. Copying it into ``pages/`` keeps a canned demo
        self-contained without putting the recording itself on a static mount.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = _safe_path(self.name, directory, ".json")

        viz_path = None
        for entry in reversed(self.events):
            if entry["event"] == "run_state":
                viz_path = entry["payload"].get("viz_path")
                break
        if viz_path and Path(viz_path).is_file():
            page = page_path(self.name, directory)
            page.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(viz_path, page)

        path.write_text(
            json.dumps(
                {
                    "version": FORMAT_VERSION,
                    "name": self.name,
                    "prompt": self.prompt,
                    "role": self.role,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "duration": self.events[-1]["t"] if self.events else 0.0,
                    "events": self.events,
                },
                indent=2,
            )
        )
        return path


def unique_name(prompt: str, role: str, directory: Path = RECORDINGS_DIR) -> str:
    """A slug for this prompt/role that does not overwrite an existing one."""
    base = f"{slugify(prompt)}-{slugify(role)}"[:56].strip("-")
    name, n = base, 2
    while (directory / f"{name}.json").exists():
        name = f"{base}-{n}"
        n += 1
    return name


# -------------------------------------------------------------------- read


def load_recording(name: str, directory: Path = RECORDINGS_DIR) -> dict:
    path = _safe_path(name, directory, ".json")
    if not path.is_file():
        raise ReplayError(f"no recording named {name!r}")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ReplayError(f"recording {name!r} is unreadable: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ReplayError(f"recording {name!r} is malformed")
    return data


def list_recordings(directory: Path = RECORDINGS_DIR) -> list[dict]:
    """Metadata for every readable recording. Never includes event payloads.

    ``role`` is part of the listing on purpose: it is what the caller needs
    to be logged in as for the replay to be served.
    """
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        if not NAME_RE.match(path.stem):
            continue
        try:
            data = load_recording(path.stem, directory)
        except ReplayError:
            continue
        out.append(
            {
                "name": data.get("name", path.stem),
                "prompt": data.get("prompt", ""),
                "role": data.get("role", ""),
                "recorded_at": data.get("recorded_at", ""),
                "duration": data.get("duration", 0.0),
                "events": len(data["events"]),
            }
        )
    return out


def _replay_payload(entry: dict, name: str, directory: Path) -> dict:
    """One recorded payload, adjusted for being served back.

    ``viz_path`` is the recorder's local filesystem path and is dropped.
    ``viz_url`` is re-pointed at the copied graph page if one was saved,
    and nulled otherwise — the frontend draws the graph from the
    ``graph`` JSON in the same payload either way, so a missing page
    costs the link, not the picture.
    """
    payload = entry["payload"]
    if entry["event"] == "run_started":
        # Flagged from the first frame, so a client can tell it is watching a
        # recording without having to remember how the stream was started.
        return {**payload, "replay": True}
    if entry["event"] != "run_state":
        return payload
    payload = {k: v for k, v in payload.items() if k != "viz_path"}
    try:
        has_page = page_path(name, directory).is_file()
    except ReplayError:
        has_page = False
    payload["viz_url"] = f"/replays/{name}.html" if has_page else None
    payload["replay"] = True
    return payload


def replay_events(
    data: dict, name: str, directory: Path = RECORDINGS_DIR, speed: float = 1.0
) -> Iterator[tuple[float, str, dict]]:
    """Yield ``(delay_before_event, event, payload)`` for a recording."""
    speed = max(MIN_SPEED, min(float(speed), MAX_SPEED))
    previous = 0.0
    for entry in data["events"]:
        gap = min(max(entry["t"] - previous, 0.0), MAX_GAP) / speed
        previous = entry["t"]
        yield gap, entry["event"], _replay_payload(entry, name, directory)


async def stream_recording(
    name: str,
    emit,
    directory: Path = RECORDINGS_DIR,
    speed: float = 1.0,
    run_id: str | None = None,
) -> None:
    """Emit a recording's events, paced as captured.

    The caller is responsible for having checked that the requesting
    principal's role matches ``data["role"]`` — see the module docstring.
    """
    data = load_recording(name, directory)
    for gap, event, payload in replay_events(data, name, directory, speed):
        if gap:
            await asyncio.sleep(gap)
        if event == "run_started" and run_id:
            payload = {**payload, "run_id": run_id}
        emit(event, payload)
