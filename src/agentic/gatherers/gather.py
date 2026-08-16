"""Gathering logic (Michael).

Given one GatherRequest, find and read plausibly-relevant files from the
department's data dir. Bias LOOSE: over-gather rather than under-gather.

Example from Malik so the rest of the pipeline runs end-to-end: delegates
to his reference filesystem gatherer in gatherer.py. Swap in your real
implementation (Gemini Flash relevance pick, etc.) here.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult


async def gather(request: GatherRequest, config: EnvironmentConfig) -> GatherResult:
    """(Michael — TODO, example below)"""
    from agentic.gatherers import gatherer  # local import keeps this swappable

    return await gatherer.gather_files(request, config)
