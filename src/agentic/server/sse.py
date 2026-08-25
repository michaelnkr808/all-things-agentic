"""SSE plumbing (provisional — Malik).

A tiny bridge between the pipeline's sync callbacks and a streaming
response. Events are `text/event-stream` frames:

    event: gatherer_result
    data: {"department": "hr", "kept": 0, "denied": 1, "errors": []}

Event names are the frontend contract (see app.py / PLAN.md):
run_started, gatherer_result, gathered, synthesized, veto, revising,
run_state, error.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

_DONE = object()


class SseQueue:
    """Sync-producer / async-consumer queue for one run's events.

    All producers run on the event loop thread (pipeline is async), so
    put_nowait is safe from callbacks like spawn_gatherers' on_result.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    def emit(self, event: str, payload: dict | None = None) -> None:
        self._queue.put_nowait((event, payload or {}))

    def close(self) -> None:
        self._queue.put_nowait(_DONE)

    async def stream(self):
        """Yield formatted SSE frames until the producer closes."""
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            yield _format(*item)


def _format(event: str, payload: dict) -> str:
    data = json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


def parse_stream(body: str) -> list[tuple[str, dict]]:
    """Test helper: parse an SSE body back into (event, payload) pairs."""
    frames = []
    for chunk in body.split("\n\n"):
        if not chunk.strip():
            continue
        name, data = None, None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if name is not None:
            frames.append((name, data))
    return frames


def emit_to(fn_by_event: dict[str, Callable[[dict], None]], event: str, payload: dict) -> None:
    """Route one event to its named handler; unknown events are dropped.

    Lets callers subscribe to only the events they care about without
    every producer knowing the full set.
    """
    handler = fn_by_event.get(event)
    if handler is not None:
        handler(payload)
