"""State manager core (Malik).

Interfaces fixed so Michael's veto loop and obsidian emitter can build
against them; bodies are Malik's.

Flow:
    plan_and_gather -> planner picks departments, spawns gatherers
    synthesize      -> synthesizer condenses results, output.emit renders
    revise          -> re-synthesize with the veto checker's revision notes
"""

from __future__ import annotations

from typing import Callable

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import CompiledOutput, GatherRequest, GatherResult, VetoVerdict
from agentic.gatherers import permissions
from agentic.gatherers.spawn import spawn_gatherers
from agentic.state_manager import output
from agentic.state_manager.manager_planning import plan_to_requests, run_planner
from agentic.state_manager.manager_synthesizer import run_synthesizer


def _dump_results(results: list[GatherResult]) -> str:
    """Serialize GatherResults into a plain-text brief for the synthesizer.

    Each file is headed by the exact ``[[department/path]]`` token the answer
    should cite it with. The synthesizer sees the department only as a section
    heading, so asking it to compose the prefix itself invites half-formed
    citations like ``[[q3-budget.csv]]`` — which carry no department and break
    the graph's file→department edge. Giving it the finished token to copy
    removes the composition step entirely.
    """
    sections = []
    for result in results:
        if not (result.files or result.denied or result.errors):
            continue
        sections.append(f"## {result.department} (request {result.request_id})")
        for file in result.files:
            citation = f"[[{file.department}/{file.path}]]"
            sections.append(
                f"\n### {citation} — {file.relevance_note}\n{file.content}"
            )
        if result.denied:
            sections.append(f"\n(denied by permissions: {', '.join(result.denied)})")
        if result.errors:
            sections.append(f"\n(errors: {', '.join(result.errors)})")
    return "\n\n".join(sections)


def _gathered_files(results: list[GatherResult]):
    return [f for r in results for f in r.files]


async def plan_and_gather(
    prompt: str,
    config: EnvironmentConfig,
    requester_role: str | None = None,
    on_result: Callable[[GatherRequest, GatherResult], None] | None = None,
    on_progress: Callable[[str, dict], None] | None = None,
) -> list[GatherResult]:
    """Parse the prompt, pick departments, spawn gatherers (see spawn.py).

    ``requester_role`` overrides the YAML principal with an authenticated
    role (server auth — provisional, Malik); it is validated fail-closed by
    permissions.load_permissions_config. Every permission gate in this run
    reads the same overridden config. ``on_result`` fires per gatherer once
    the spawn gate has stripped it (see spawn.spawn_gatherers), and
    ``on_progress`` narrates each gatherer's work file by file.
    """
    perms = permissions.load_permissions_config(principal_role=requester_role)
    plan = await run_planner(prompt, config)
    requests = plan_to_requests(plan, config, requester_role=perms.principal.role)
    return await spawn_gatherers(
        requests,
        config,
        permissions_cfg=perms,
        on_result=on_result,
        on_progress=on_progress,
    )


async def synthesize(
    prompt: str, results: list[GatherResult], config: EnvironmentConfig
) -> CompiledOutput:
    """Combine gathered files into an answer, then output.emit(...)."""
    answer_body = await run_synthesizer(prompt, _dump_results(results), config.models)
    return output.emit(prompt, answer_body, _gathered_files(results), revision=0)


async def revise(
    prompt: str,
    previous: CompiledOutput,
    verdict: VetoVerdict,
    results: list[GatherResult],
    config: EnvironmentConfig,
) -> CompiledOutput:
    """Redo synthesis applying verdict.revision_notes; bump revision.

    Called by Michael's veto retry loop in pipeline.py.
    """
    brief = _dump_results(results)
    if verdict.revision_notes:
        brief += f"\n\nRevision notes from the veto checker:\n{verdict.revision_notes}"
    answer_body = await run_synthesizer(prompt, brief, config.models)
    return output.emit(
        prompt, answer_body, _gathered_files(results), revision=previous.revision + 1
    )
