"""End-to-end orchestration, including the veto retry loop (Michael owns the loop).

    config  = load_config("config/environment.yaml")
    results = await manager.plan_and_gather(prompt, config)
    compiled = await manager.synthesize(prompt, results, config)
    for _ in range(config.veto.max_retries + 1):
        verdict = await checker.check(prompt, compiled, config)
        if verdict.approved:
            break
        compiled = await manager.revise(prompt, compiled, verdict, results, config)
    viz.render_html(compiled, out_path)   # warn in the page if never approved
"""

from __future__ import annotations

from pathlib import Path

from agentic.contracts.config import load_config
from agentic.contracts.messages import CompiledOutput
from agentic.state_manager import manager
from agentic.veto import checker
from agentic.client import viz


async def run(prompt: str, config_path: str = "config/environment.yaml") -> CompiledOutput:
    """(Michael — TODO: wire the loop above once pieces land)"""
    raise NotImplementedError
