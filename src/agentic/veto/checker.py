"""Adversarial veto checker (Michael).

Gets the original prompt, the environment config, and the CompiledOutput.
Its job is to try to reject: unsupported claims, missing [[source]] links,
departments used that the config doesn't allow, prompt not actually
answered. Returns a VetoVerdict; on veto, revision_notes must be concrete
enough for state_manager.revise() to act on.

Runs on the strong model (config.models.veto) with an adversarial system
prompt — it should APPROVE only when it fails to find a real problem.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import CompiledOutput, VetoVerdict


async def check(prompt: str, compiled: CompiledOutput, config: EnvironmentConfig) -> VetoVerdict:
    """(Michael — TODO)"""
    raise NotImplementedError("Michael: veto checker")
