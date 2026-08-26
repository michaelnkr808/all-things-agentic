"""Retry policy for model calls, shared by every stage of the pipeline.

Gemini Flash returns 503 UNAVAILABLE under load and 429 RESOURCE_EXHAUSTED
when a rate limit is hit; both are transient and both have already cost live
runs. The planner is the worst place to lose one — it runs first, so an
unretried blip kills the whole run before a single file is read.

Two failures look alike and must not be treated alike:

* a *rate* limit says "retry in 39s" and clears on its own — worth waiting for;
* a *daily* free-tier quota (20 requests per day per model, reported with a
  'PerDay' quotaId) will not clear before tomorrow. Backing off just spends a
  minute to fail anyway, so it is raised immediately as QuotaExhausted with a
  message naming the fix.
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

ATTEMPTS = 3
BASE_DELAY = 1.5
MAX_DELAY = 45.0  # rate limits ask for ~40s; cap so a run cannot hang


class QuotaExhausted(RuntimeError):
    """A per-day model quota is spent; retrying cannot help."""


def is_daily_quota(exc: BaseException) -> bool:
    """True for a per-day free-tier cap, which no amount of backoff clears."""
    return "PerDay" in str(exc)


def is_transient(exc: BaseException) -> bool:
    """503/429/overloaded — worth retrying. A bad key or bad request is not."""
    if is_daily_quota(exc):
        return False
    if getattr(exc, "code", None) in (429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("unavailable", "overloaded", "resource_exhausted", "try again")
    )


def retry_after(exc: BaseException, fallback: float) -> float:
    """Honour the server's own retryDelay when it sends one.

    Backing off for less than the server asked just burns another attempt
    against the same window. Capped, so one hostile value cannot stall a run.
    """
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", str(exc))
    if match:
        return min(float(match.group(1)) + 0.5, MAX_DELAY)
    return fallback


def _quota_message(what: str, exc: BaseException) -> str:
    model = re.search(r"model: ([\w.\-]+)", str(exc))
    limit = re.search(r"limit: (\d+)", str(exc))
    return (
        f"{what}: daily free-tier quota exhausted"
        + (f" for {model.group(1)}" if model else "")
        + (f" (limit {limit.group(1)}/day)" if limit else "")
        + " — enable billing on the Google Cloud project, point google-genai at "
        "Vertex AI, or switch models in config/environment.yaml (each model has "
        "its own daily allowance)."
    )


async def with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    what: str,
    attempts: int = ATTEMPTS,
    base_delay: float = BASE_DELAY,
) -> T:
    """Await `call()`, retrying transient model failures with backoff.

    `what` names the stage ("planner", "gatherer", "synthesizer") so a failure
    says which one gave up. Non-transient errors propagate untouched on the
    first attempt — this must never mask a bad API key as a flaky network.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await call()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_daily_quota(exc):
                raise QuotaExhausted(_quota_message(what, exc)) from exc
            last = exc
            if attempt == attempts - 1 or not is_transient(exc):
                raise
            # Gatherers run concurrently, so a spike tends to hit all of them
            # at once; spreading the retries out is the point of the backoff.
            await asyncio.sleep(retry_after(exc, base_delay * (2**attempt)))
    raise last  # unreachable; keeps type checkers honest


__all__ = [
    "QuotaExhausted",
    "is_daily_quota",
    "is_transient",
    "retry_after",
    "with_retry",
]
