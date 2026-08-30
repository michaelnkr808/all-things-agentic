"""Request/response schemas for the HTTP surface (provisional — Malik).

These are what Michael's frontend builds against. Changing any field is
a contract change — ping first.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1)

    # NOTE: deliberately no client-supplied config path — that would let a
    # request point the pipeline at arbitrary local files. Operators set
    # AGENTIC_CONFIG_PATH instead.

    # Per-run overrides (Michael, frontend controls). Every one is bounded and
    # applied to a *copy* of the config in runs.apply_overrides — none of them
    # can widen what a run may reach:
    #   - the ints are clamped to the operator's configured ceiling
    #   - veto_model must appear in models.veto_choices (an allowlist)
    #   - departments only ever narrows; the permission gates still decide
    #     the upper bound, so this cannot grant access to anything new
    max_gatherers: int | None = Field(default=None, ge=1, le=64)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    veto_model: str | None = None
    departments: list[str] | None = None


class CompareRequest(RunRequest):
    """POST /api/compare — the same prompt under two roles.

    `as_role` must be a role that sees strictly fewer departments than the
    caller (server/compare.py::check_comparable). It is a comparison, never
    an impersonation: both sides run through every gate for their own role.
    """

    as_role: str = Field(min_length=1)


class FleetResponse(BaseModel):
    """GET /api/fleet — what this principal may see, before any run.

    Read-only introspection so the frontend can show the fleet, its
    permission boundaries and its cloud backends without spending a model
    call. `readable` is computed for the caller's authenticated role.
    """

    role: str
    departments: list["FleetDepartment"]
    models: dict[str, str]
    veto_choices: list[str] = Field(default_factory=list)
    #: Roles this principal may run a side-by-side comparison against.
    compare_roles: list[str] = Field(default_factory=list)
    max_gatherers: int
    max_files_per_gatherer: int
    max_retries: int


class FleetDepartment(BaseModel):
    name: str
    readable: bool
    storage: str | None = None  # "gcs" / "drive", or None for local files
    file_globs: list[str] = Field(default_factory=list)
