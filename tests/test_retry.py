"""Shared model-call retry policy.

Transient blips (503, rate limits) cost live runs during development; a daily
free-tier quota is a different animal and must not be retried at all.
"""

import pytest

from agentic.retry import (
    QuotaExhausted,
    is_daily_quota,
    is_transient,
    retry_after,
    with_retry,
)


class Boom(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


RATE_LIMITED = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'quota', "
    "'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '39s'}]}}"
)
DAILY_QUOTA = (
    "429 RESOURCE_EXHAUSTED. {'violations': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}], "
    "'message': 'limit: 20, model: gemini-3-flash'}"
)


def test_transient_classification():
    assert is_transient(Boom("503 UNAVAILABLE"))
    assert is_transient(Boom("model is overloaded"))
    assert is_transient(Boom("boom", code=503))
    # A bad key is a bug, not weather — retrying just delays the real error.
    assert not is_transient(Boom("401 API key not valid"))
    assert not is_transient(Boom("400 INVALID_ARGUMENT"))


def test_daily_quota_is_not_transient():
    assert is_daily_quota(Boom(DAILY_QUOTA))
    assert not is_transient(Boom(DAILY_QUOTA))
    assert not is_daily_quota(Boom(RATE_LIMITED))


def test_retry_after_prefers_the_servers_own_delay():
    assert retry_after(Boom(RATE_LIMITED), 1.5) == pytest.approx(39.5)
    assert retry_after(Boom("503 unavailable"), 3.0) == 3.0
    # A hostile or absurd value must not stall the run.
    assert retry_after(Boom("{'retryDelay': '9999s'}"), 1.0) == 45.0


async def test_retries_transient_failures_then_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Boom("503 UNAVAILABLE")
        return "ok"

    assert await with_retry(flaky, what="planner") == "ok"
    assert calls["n"] == 3


async def test_gives_up_after_the_attempt_budget(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    async def always_down():
        calls["n"] += 1
        raise Boom("503 UNAVAILABLE")

    with pytest.raises(Boom):
        await with_retry(always_down, what="gatherer", attempts=3)
    assert calls["n"] == 3


async def test_non_transient_failure_is_not_retried():
    calls = {"n": 0}

    async def bad_key():
        calls["n"] += 1
        raise Boom("401 API key not valid")

    with pytest.raises(Boom):
        await with_retry(bad_key, what="planner")
    assert calls["n"] == 1, "a bad key must surface immediately"


async def test_daily_quota_fails_fast_with_an_actionable_message():
    calls = {"n": 0}

    async def exhausted():
        calls["n"] += 1
        raise Boom(DAILY_QUOTA)

    with pytest.raises(QuotaExhausted) as excinfo:
        await with_retry(exhausted, what="planner")

    assert calls["n"] == 1, "waiting cannot clear a per-day quota"
    message = str(excinfo.value)
    assert "planner" in message
    assert "gemini-3-flash" in message
    assert "20/day" in message
    assert "billing" in message or "Vertex" in message


async def _no_sleep(_seconds):
    return None
