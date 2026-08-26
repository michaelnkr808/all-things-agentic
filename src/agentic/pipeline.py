"""End-to-end orchestration, including the veto retry loop (Michael owns the loop).

    config   = load_config("config/environment.yaml")
    results  = await manager.plan_and_gather(prompt, config)
    compiled = await manager.synthesize(prompt, results, config)
    for attempt in range(config.veto.max_retries + 1):
        verdict = await checker.check(prompt, compiled, config, results)
        if verdict.approved or attempt == config.veto.max_retries:
            break
        compiled = await manager.revise(prompt, compiled, verdict, results, config)
    return RunResult(compiled=..., verdict=..., attempts=...)

`run()` returns the RunResult rather than only the answer, so the caller can
tell an approved answer from one that exhausted its retries — viz.py needs
that to warn on the page. Rendering is opt-out (`out_path=None`) so the
pipeline stays testable before the visualizer exists.

Two additive kwargs serve the server (see PLAN.md "AUTH + BACKEND"), and
both default to today's CLI behaviour:

- ``requester_role`` — the authenticated principal from the JWT, threaded
  into every permission gate. None keeps the YAML principal.
- ``emit(event, payload)`` — structured progress for SSE, fired alongside
  the stderr logs. None keeps stderr-only logging.

There is one veto retry loop and it lives here; the server calls this
function rather than reimplementing it.

Run it:  PYTHONPATH=src .venv/bin/python -m agentic.pipeline "your prompt"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from agentic.contracts.config import EnvironmentConfig, load_config
from agentic.contracts.messages import CompiledOutput, GatherRequest, GatherResult, VetoVerdict
from agentic.env import load_env
from agentic.state_manager import manager
from agentic.veto import checker
from agentic.client import viz

DEFAULT_OUT = Path("out/graph.html")

#: emit(event_name, payload). Event names are the SSE contract in PLAN.md:
#: run_started, gatherer_result, gathered, synthesized, veto, revising,
#: run_state, error.
Emit = Callable[[str, dict], None]


class RunResult(BaseModel):
    """What one pipeline run produced. Pipeline -> viz only, so it lives here
    rather than in contracts/ — it never crosses the Michael/Malik seam."""

    compiled: CompiledOutput
    verdict: VetoVerdict
    attempts: int
    viz_path: str | None = None


def _log(message: str) -> None:
    """Progress goes to stderr so stdout stays the answer alone (pipeable)."""
    print(message, file=sys.stderr, flush=True)


async def run(
    prompt: str,
    config_path: str = "config/environment.yaml",
    out_path: Path | None = DEFAULT_OUT,
    requester_role: str | None = None,
    emit: Emit | None = None,
    config: EnvironmentConfig | None = None,
) -> RunResult:
    """Plan, gather, synthesize, then check until approved or out of retries.

    ``config`` skips loading `config_path` — the server hands in a per-run
    copy carrying the caller's overrides, so two concurrent runs never see
    each other's knobs.
    """

    def _emit(event: str, payload: dict) -> None:
        if emit is not None:
            emit(event, payload)

    try:
        return await _run(prompt, config_path, out_path, requester_role, _emit, config)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The consumer of a stream cannot see a traceback; give it the failure
        # as an event, then let the exception propagate to the caller as usual.
        _emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        raise


async def _run(
    prompt: str,
    config_path: str,
    out_path: Path | None,
    requester_role: str | None,
    emit: Emit,
    config: EnvironmentConfig | None = None,
) -> RunResult:
    emit("run_started", {"prompt": prompt, "role": requester_role})

    if config is None:
        config = load_config(config_path)
    _log(f"config: {len(config.departments)} departments, "
         f"max_gatherers={config.gatherers.max_gatherers}, "
         f"max_retries={config.veto.max_retries}")

    def on_result(request: GatherRequest, result: GatherResult) -> None:
        """Fires per gatherer, after the spawn gate has stripped the result."""
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
        prompt,
        config,
        requester_role=requester_role,
        on_result=on_result,
        # Per-file narration goes straight out as events; the department is in
        # every payload, so interleaved gatherers stay distinguishable.
        on_progress=emit,
    )
    gathered = sum(len(r.files) for r in results)
    denied = sum(len(r.denied) for r in results)
    errors = sum(len(r.errors) for r in results)
    _log(f"gathered: {gathered} file(s) from {len(results)} gatherer(s) "
         f"({denied} denied, {errors} error(s))")
    for r in results:
        _log(f"  - {r.department}: {len(r.files)} kept, {len(r.denied)} denied")
    emit(
        "gathered",
        {
            "gatherers": len(results),
            "kept": gathered,
            "denied": denied,
            "departments": [r.department for r in results],
        },
    )

    compiled = await manager.synthesize(prompt, results, config)
    emit("synthesized", {"revision": compiled.revision})

    # Check, and revise only when another check will actually follow. Revising
    # on the final attempt pays for a synthesis nobody reviews.
    for attempt in range(config.veto.max_retries + 1):
        verdict = await checker.check(prompt, compiled, config, results)
        _log(f"veto check {attempt + 1}: "
             f"{'APPROVED' if verdict.approved else 'VETOED'}")
        for reason in verdict.reasons:
            _log(f"    {reason}")
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

    if not verdict.approved:
        _log(f"shipping UNAPPROVED after {attempt + 1} check(s) — "
             f"retry budget exhausted")

    viz_path: str | None = None
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        viz.render_html(compiled, out_path, results=results, verdict=verdict)
        viz_path = str(out_path)
        _log(f"wrote {out_path}")

    result = RunResult(
        compiled=compiled, verdict=verdict, attempts=attempt + 1, viz_path=viz_path
    )
    # The same builder the standalone page uses, so the live frontend draws an
    # identical graph instead of reimplementing the node vocabulary in JS.
    emit("run_state", _run_state(result, viz.build_graph(compiled, results, verdict)))
    return result


def _run_state(result: RunResult, graph: dict) -> dict:
    """The final SSE payload: everything a client needs to render the answer."""
    return {
        "approved": result.verdict.approved,
        "attempts": result.attempts,
        "obsidian_markdown": result.compiled.obsidian_markdown,
        "sources": list(result.compiled.sources),
        "departments_used": list(result.compiled.departments_used),
        "revision": result.compiled.revision,
        "reasons": list(result.verdict.reasons),
        "viz_path": result.viz_path,
        "graph": graph,
    }


def main(argv: list[str] | None = None) -> int:
    load_env()  # .env in the repo root supplies the API keys

    parser = argparse.ArgumentParser(
        prog="agentic.pipeline", description="Run the full agentic pipeline."
    )
    parser.add_argument("prompt", help="the question to answer")
    parser.add_argument("--config", default="config/environment.yaml")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help="HTML graph output path"
    )
    parser.add_argument(
        "--role",
        default=None,
        help="run as this role instead of the permissions.yaml principal",
    )
    parser.add_argument(
        "--no-viz",
        action="store_true",
        help="skip rendering (use while client/viz.py is unimplemented)",
    )
    args = parser.parse_args(argv)

    result = asyncio.run(
        run(
            args.prompt,
            config_path=args.config,
            out_path=None if args.no_viz else Path(args.out),
            requester_role=args.role,
        )
    )

    print(result.compiled.obsidian_markdown)
    return 0 if result.verdict.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
