"""Gathering logic (Michael).

Given one GatherRequest, find and read plausibly-relevant files from the
department's data dir. Bias LOOSE: over-gather rather than under-gather.

Approach:
1. List files matching the department's file_globs.
2. One cheap Gemini Flash call: here's the listing + the query/keywords,
   return every path that might be relevant (not just the best ones).
3. Read those files (respecting max_files), attach a one-line relevance_note.
4. Route each candidate through permissions.check(...) (Malik) — denied
   paths go in GatherResult.denied, not files.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import GatherRequest, GatherResult


async def gather(request: GatherRequest, config: EnvironmentConfig) -> GatherResult:
    """(Michael — TODO)"""
    raise NotImplementedError("Michael: gathering")
