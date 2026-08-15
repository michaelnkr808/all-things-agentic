"""State manager core (Malik).

Interfaces fixed so Michael's veto loop and obsidian emitter can build
against them; bodies are Malik's.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import CompiledOutput, GatherResult, VetoVerdict


async def plan_and_gather(prompt: str, config: EnvironmentConfig) -> list[GatherResult]:
    """Parse the prompt, pick departments, spawn gatherers (see spawn.py). (Malik)"""
    raise NotImplementedError("Malik: spin up gatherers")


async def synthesize(prompt: str, results: list[GatherResult], config: EnvironmentConfig) -> CompiledOutput:
    """Combine gathered files into an answer, then output.emit(...). (Malik)"""
    raise NotImplementedError("Malik: synthesize resources")


async def revise(prompt: str, previous: CompiledOutput, verdict: VetoVerdict,
                 results: list[GatherResult], config: EnvironmentConfig) -> CompiledOutput:
    """Redo synthesis applying verdict.revision_notes; bump revision. (Malik)

    Called by Michael's veto retry loop in pipeline.py.
    """
    raise NotImplementedError("Malik: revise after veto")
