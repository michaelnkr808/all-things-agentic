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

Run it:  PYTHONPATH=src .venv/bin/python -m agentic.pipeline "your prompt"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agentic.contracts.config import load_config
from agentic.contracts.messages import RunResult
from agentic.state_manager import manager
from agentic.veto import checker
from agentic.client import viz

DEFAULT_OUT = Path("out/graph.html")


def _log(message: str) -> None:
    """Progress goes to stderr so stdout stays the answer alone (pipeable)."""
    print(message, file=sys.stderr, flush=True)


async def run(
    prompt: str,
    config_path: str = "config/environment.yaml",
    out_path: Path | None = DEFAULT_OUT,
) -> RunResult:
    """Plan, gather, synthesize, then check until approved or out of retries."""
    config = load_config(config_path)
    _log(f"config: {len(config.departments)} departments, "
         f"max_gatherers={config.gatherers.max_gatherers}, "
         f"max_retries={config.veto.max_retries}")

    results = await manager.plan_and_gather(prompt, config)
    gathered = sum(len(r.files) for r in results)
    denied = sum(len(r.denied) for r in results)
    errors = sum(len(r.errors) for r in results)
    _log(f"gathered: {gathered} file(s) from {len(results)} gatherer(s) "
         f"({denied} denied, {errors} error(s))")
    for r in results:
        _log(f"  - {r.department}: {len(r.files)} kept, {len(r.denied)} denied")

    compiled = await manager.synthesize(prompt, results, config)

    # Check, and revise only when another check will actually follow. Revising
    # on the final attempt pays for a synthesis nobody reviews.
    for attempt in range(config.veto.max_retries + 1):
        verdict = await checker.check(prompt, compiled, config, results)
        _log(f"veto check {attempt + 1}: "
             f"{'APPROVED' if verdict.approved else 'VETOED'}")
        for reason in verdict.reasons:
            _log(f"    {reason}")
        if verdict.approved or attempt == config.veto.max_retries:
            break
        compiled = await manager.revise(prompt, compiled, verdict, results, config)

    if not verdict.approved:
        _log(f"shipping UNAPPROVED after {attempt + 1} check(s) — "
             f"retry budget exhausted")

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        viz.render_html(compiled, out_path)
        _log(f"wrote {out_path}")

    return RunResult(compiled=compiled, verdict=verdict, attempts=attempt + 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic.pipeline", description="Run the full agentic pipeline."
    )
    parser.add_argument("prompt", help="the question to answer")
    parser.add_argument("--config", default="config/environment.yaml")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help="HTML graph output path"
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
        )
    )

    print(result.compiled.obsidian_markdown)
    return 0 if result.verdict.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
