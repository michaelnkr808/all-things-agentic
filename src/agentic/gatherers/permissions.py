"""Permission enforcement (Malik).

The permissions config (config/permissions.yaml) is the trusted source of
the session's principal role and the role->department access map. The
environment config's per-department ``allowed_roles`` is a second,
independent grant. A role may read a department only when BOTH grant
access (AND semantics) — any disagreement fails closed to deny.

The mandatory, non-bypassable gate lives in spawn.spawn_gatherers; the
gatherer's own pre-read check here is the first (efficiency) layer.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from agentic.contracts.config import ConfigError, DepartmentConfig

DEFAULT_PERMISSIONS_CONFIG = "config/permissions.yaml"


class Principal(BaseModel):
    """Trusted identity of the session that launched the gatherers."""

    role: str


class RoleAccess(BaseModel):
    """What a single role may touch, per the permissions config."""

    departments: list[str] = Field(default_factory=list)


class PermissionsConfig(BaseModel):
    principal: Principal
    roles: dict[str, RoleAccess]

    def role_known(self, role: str) -> bool:
        return role in self.roles

    def departments_for_role(self, role: str) -> list[str]:
        access = self.roles.get(role)
        return access.departments if access else []


def load_permissions_config(path: str | Path = DEFAULT_PERMISSIONS_CONFIG) -> PermissionsConfig:
    """Parse and validate config/permissions.yaml.

    Fails fast (ConfigError) if the file is missing, is invalid YAML,
    fails schema validation, or names a principal role that is not defined
    under ``roles``. Failing closed is the point: an unparseable
    permissions config must never widen gatherer access.
    """
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Permissions config not found at: {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    try:
        cfg = PermissionsConfig.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{' -> '.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        raise ConfigError(f"Invalid permissions config in {path}:\n" + "\n".join(lines)) from e

    if not cfg.role_known(cfg.principal.role):
        raise ConfigError(
            f"Invalid permissions config in {path}: principal role "
            f"{cfg.principal.role!r} is not defined under 'roles'."
        )

    return cfg


def check(path: str, requester_role: str, department: DepartmentConfig) -> bool:
    """True if `requester_role` may read `path` in this department.

    Three checks, all must pass:
    1. the role is on the department's allow-list,
    2. the path is non-empty and not '.', and
    3. the path resolves strictly inside the department's data dir
       (rejects ``..`` escapes, absolute paths, both separator styles,
       and symlinks that point outside the root).
    """
    if requester_role not in department.allowed_roles:
        return False

    if not path or not path.strip():
        return False

    raw = Path(path.replace("\\", "/"))
    if raw.is_absolute():
        return False

    root = Path(department.path).resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate != root


def allowed(
    path: str,
    role: str,
    department: DepartmentConfig,
    permissions_cfg: PermissionsConfig,
) -> bool:
    """Full gate: permissions-config grant AND env-config grant AND path containment.

    Any single disagreement denies (fails closed). This is what the
    spawner applies to every file a gatherer returns.
    """
    if not permissions_cfg.role_known(role):
        return False
    if department.name not in permissions_cfg.departments_for_role(role):
        return False
    return check(path, role, department)
