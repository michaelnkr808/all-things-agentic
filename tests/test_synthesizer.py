"""Synthesizer tests (Malik).

Only the failure guard for now — the happy path needs a live model.
"""

from types import SimpleNamespace

import pytest

from agentic.contracts.config import load_config
from agentic.state_manager import manager_synthesizer


async def test_run_synthesizer_empty_response_raises_clearly(monkeypatch):
    """A keyless/blocked model call used to die as 'NoneType has no parts'."""
    config = load_config("config/environment.example.yaml")

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            yield SimpleNamespace(is_final_response=lambda: True, content=None)

    monkeypatch.setattr(manager_synthesizer, "Runner", FakeRunner)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        await manager_synthesizer.run_synthesizer("q", "material", config.models)
