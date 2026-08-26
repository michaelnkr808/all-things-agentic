"""Run execution for the server (provisional — Malik).

Thin adapter over ``pipeline.run()``. The replica of Michael's veto retry
loop that used to live here is gone: pipeline.run now takes the additive
``requester_role`` / ``emit`` kwargs it was waiting on, so there is exactly
one orchestration path and the SSE event names come straight from it.

What this module still owns — the parts that are genuinely server
concerns, not pipeline concerns:
- requester_role comes from the verified JWT, not config/permissions.yaml.
- each run renders its own graph page under out/graphs/ instead of one
  fixed out/graph.html, and the filesystem path is mapped to a URL the
  browser can fetch from the /graphs mount.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Callable

from agentic import pipeline
from agentic.contracts.config import ConfigError, EnvironmentConfig, load_config
from agentic.gatherers import permissions
from agentic.gatherers.permissions import PermissionsConfig, load_permissions_config
from agentic.server.schemas import FleetDepartment, FleetResponse

DEFAULT_CONFIG_PATH = "config/environment.yaml"
GRAPHS_DIR = Path("out/graphs")

Emit = Callable[[str, dict], None]


class OverrideError(ValueError):
    """A per-run override the server refuses (unknown name, not allowlisted)."""


def apply_overrides(
    config: EnvironmentConfig,
    *,
    max_gatherers: int | None = None,
    max_retries: int | None = None,
    veto_model: str | None = None,
    departments: list[str] | None = None,
) -> EnvironmentConfig:
    """Return a copy of `config` with the client's run knobs applied.

    A copy, never the loaded config: concurrent runs must not see each
    other's settings. Every override is bounded so none of them can widen
    what a run reaches:

    - the ints are clamped to the operator's configured ceiling, so a
      client cannot ask for more parallelism or more retries than the
      deployment allows;
    - `veto_model` must be one the operator listed in models.veto_choices —
      it arrives from a browser and ends up as a model id, so free text is
      not acceptable;
    - `departments` only narrows. Restricting the department list means the
      planner never sees the others; it cannot grant anything, because the
      permission gates independently decide the upper bound. An unknown
      name is rejected rather than ignored, so a typo fails loudly instead
      of silently producing an emptier answer.
    """
    config = config.model_copy(deep=True)

    if max_gatherers is not None:
        config.gatherers.max_gatherers = max(
            1, min(max_gatherers, config.gatherers.max_gatherers)
        )
    if max_retries is not None:
        config.veto.max_retries = max(0, min(max_retries, config.veto.max_retries))

    if veto_model is not None:
        if veto_model not in config.models.veto_choices:
            raise OverrideError(
                f"veto_model {veto_model!r} is not offered; "
                f"choices: {config.models.veto_choices or '(none configured)'}"
            )
        config.models.veto = veto_model

    if departments is not None:
        known = {d.name for d in config.departments}
        unknown = [d for d in departments if d not in known]
        if unknown:
            raise OverrideError(f"unknown department(s): {', '.join(sorted(unknown))}")
        if not departments:
            raise OverrideError("at least one department must stay selected")
        keep = set(departments)
        config.departments = [d for d in config.departments if d.name in keep]

    return config


def build_fleet(role: str, config: EnvironmentConfig) -> FleetResponse:
    """What this principal may see, before any run — no model calls.

    `readable` is the department half of the permission gate, computed with
    permissions.department_allowed so this view can never disagree with what
    the gatherers will actually enforce.
    """
    perms = load_permissions_config(principal_role=role)
    return FleetResponse(
        role=perms.principal.role,
        departments=[
            FleetDepartment(
                name=d.name,
                readable=permissions.department_allowed(perms.principal.role, d, perms),
                storage=d.storage.provider if d.storage else None,
                file_globs=list(d.file_globs),
            )
            for d in config.departments
        ],
        models={
            "state_manager": config.models.state_manager,
            "gatherer": config.models.gatherer,
            "veto": config.models.veto,
        },
        veto_choices=list(config.models.veto_choices),
        max_gatherers=config.gatherers.max_gatherers,
        max_files_per_gatherer=config.gatherers.max_files_per_gatherer,
        max_retries=config.veto.max_retries,
    )


def _slug(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
    return words[:32] or "run"


def _graph_path(prompt: str) -> Path:
    """A unique page per run, so two viewers never overwrite each other."""
    name = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{_slug(prompt)}.html"
    return GRAPHS_DIR / name


def _with_viz_url(payload: dict) -> dict:
    """Rewrite the pipeline's filesystem viz_path into a browser URL."""
    viz_path = payload.get("viz_path")
    if not viz_path:
        return {**payload, "viz_url": None}
    return {**payload, "viz_url": f"/graphs/{Path(viz_path).name}"}


async def execute_run(
    prompt: str,
    requester_role: str,
    emit: Emit,
    config_path: str = DEFAULT_CONFIG_PATH,
    config: EnvironmentConfig | None = None,
) -> dict:
    """Run the full pipeline for an authenticated principal, emitting events.

    ``config`` is the per-run copy from apply_overrides; omit it and the
    pipeline loads `config_path` unchanged.

    Returns the final ``run_state`` payload (also emitted as the last event).
    Raises whatever the pipeline raises after emitting an ``error`` event —
    callers should not need a second error channel.
    """
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    state: dict = {}

    def _emit(event: str, payload: dict) -> None:
        if event == "run_state":
            payload = _with_viz_url(payload)
            state.update(payload)
        emit(event, payload)

    await pipeline.run(
        prompt,
        config_path=config_path,
        out_path=_graph_path(prompt),
        requester_role=requester_role,
        emit=_emit,
        config=config,
    )

    return state


def permissions_for_request(requester_role: str) -> PermissionsConfig:
    """The overridden gate identity for a request (used by app wiring/tests)."""
    return load_permissions_config(principal_role=requester_role)


__all__ = [
    "execute_run",
    "permissions_for_request",
    "apply_overrides",
    "build_fleet",
    "load_config",
    "OverrideError",
    "ConfigError",
]
