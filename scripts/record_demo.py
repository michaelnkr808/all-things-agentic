"""Record a live run to recordings/ so it can be replayed with no API calls.

    PYTHONPATH=src .venv/bin/python scripts/record_demo.py \
        --role admin "Compare Q3 engineering spend to the finance budget"

This is the same pipeline the server drives, with the same emit callback, so
what lands on disk is byte-for-byte the event stream the browser would have
received. Costs one real run; afterwards the demo is free and offline.

The role matters: it is recorded alongside the events and the server refuses
to replay a recording to a principal with a different role. Record as `admin`
for the run that answers everything, and as `analyst` for the run where HR
comes back locked — the two together are the whole permission story.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agentic.env import load_env
from agentic.server import replay, runs


async def record(prompt: str, role: str, name: str | None, config_path: str,
                 quiet: bool = False) -> Path:
    directory = replay.RECORDINGS_DIR
    recorder = replay.Recorder(
        prompt=prompt,
        role=role,
        name=name or replay.unique_name(prompt, role, directory),
    )

    def show(event: str, payload: dict) -> None:
        if quiet:
            return
        dept = payload.get("department")
        detail = f" {dept}" if dept else ""
        path = payload.get("path")
        if path:
            detail += f" {path}"
        print(f"  {event}{detail}", file=sys.stderr)

    await runs.execute_run(
        prompt,
        role,
        emit=recorder.wrap(show),
        config_path=config_path,
    )
    return recorder.save(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--role", default="admin",
                        help="role to run as; the replay is pinned to it")
    parser.add_argument("--name", default=None,
                        help="recording slug (default: derived from the prompt)")
    parser.add_argument("--config", default=runs.DEFAULT_CONFIG_PATH)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    load_env()
    try:
        path = asyncio.run(
            record(args.prompt, args.role, args.name, args.config, args.quiet)
        )
    except Exception as e:  # a failed run should not print a traceback wall
        print(f"recording failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    data = replay.load_recording(path.stem)
    print(f"wrote {path} — {len(data['events'])} events, "
          f"{data['duration']:.1f}s, role={data['role']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
