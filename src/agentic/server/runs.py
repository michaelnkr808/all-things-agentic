"""Run execution for the server (provisional — Malik).

PROVISIONAL: this module carries a replica of pipeline.run()'s veto retry
loop because pipeline.py is Michael's file and the additive kwargs he owns
(``requester_role`` / ``emit``) are flagged in PLAN.md, not yet landed.
When Michael's version merges, delete execute_run()'s orchestration and
call pipeline.run(...) instead — the SSE event names stay identical.

Differences from the CLI path (deliberate):
- requester_role comes from the verified JWT, not config/permissions.yaml.
- progress goes to an emit callback (SSE) instead of stderr logs.
- viz renders per-run under out/graphs/ and its URL is returned, rather
  than one fixed out/graph.html.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Callable

from agentic.client import viz
from agentic.contracts.config import ConfigError, load_config
from agentic.gatherers.permissions import PermissionsConfig, load_permissions_config
from agentic.state_manager import manager
from agentic.veto import checker

DEFAULT_CONFIG_PATH = "config/environment.yaml"
GRAPHS_DIR = Path("out/graphs")

Emit = Callable[[str, dict], None]


def _slug(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    return words[:32] or "run"


def _render_viz(compiled, results, verdict) -> str:
    """Write this run's graph page; return its URL path."""
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{_slug(compiled.prompt)}.html"
    out_path = GRAPHS_DIR / name
    viz.render_html(compiled, out_path, results=results, verdict=verdict)
    return f"/graphs/{name}"


async def execute_run(
    prompt: str,
    requester_role: str,
    emit: Emit,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> dict:
    """Run the full pipeline for an authenticated principal, emitting events.

    Returns the final ``run_state`` payload (also emitted as the last event).
    Raises whatever the pipeline raises after emitting an ``error`` event —
    callers should not need a second error channel.
    """
    emit("run_started", {"prompt": prompt, "role": requester_role})
    try:
        return await _run_inner(prompt, requester_role, emit, config_path)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        emit("error", {"message": f"{type(e).__name__}: {e}"})
        raise


async def _run_inner(
    prompt: str,
    requester_role: str,
    emit: Emit,
    config_path: str,
) -> dict:
    config = load_config(config_path)
    perms = load_permissions_config(principal_role=requester_role)
    role = perms.principal.role

    def on_result(request, result) -> None:
        emit(
            "gatherer_result",
            {
                "department": result.department,
                "kept": len(result.files),
                "denied": list(result.denied),
                "errors": list(result.errors),
            },
        )

    results = await manager.plan_and_gather(
        prompt, config, requester_role=role, on_result=on_result
    )
    kept = sum(len(r.files) for r in results)
    denied = sum(len(r.denied) for r in results)
    emit(
        "gathered",
        {
            "gatherers": len(results),
            "kept": kept,
            "denied": denied,
            "departments": [r.department for r in results],
        },
    )

    compiled = await manager.synthesize(prompt, results, config)
    emit("synthesized", {"revision": compiled.revision})

    # PROVISIONAL replica of pipeline.run()'s veto retry loop (see module docstring).
    attempt = 0
    for attempt in range(config.veto.max_retries + 1):
        verdict = await checker.check(prompt, compiled, config, results)
        emit(
            "veto",
            {
                "attempt": attempt + 1,
                "approved": verdict.approved,
                "reasons": list(verdict.reasons),
            },
        )
        if verdict.approved or attempt == config.veto.max_retries:
            break
        emit("revising", {"attempt": attempt + 1, "notes": verdict.revision_notes})
        compiled = await manager.revise(prompt, compiled, verdict, results, config)
        emit("synthesized", {"revision": compiled.revision})

    viz_url = _render_viz(compiled, results, verdict)

    state = {
        "approved": verdict.approved,
        "attempts": attempt + 1,
        "obsidian_markdown": compiled.obsidian_markdown,
        "sources": list(compiled.sources),
        "departments_used": list(compiled.departments_used),
        "revision": compiled.revision,
        "reasons": list(verdict.reasons),
        "viz_url": viz_url,
    }
    emit("run_state", state)
    return state


def permissions_for_request(requester_role: str) -> PermissionsConfig:
    """The overridden gate identity for a request (used by app wiring/tests)."""
    return load_permissions_config(principal_role=requester_role)


__all__ = ["execute_run", "permissions_for_request", "ConfigError"]
