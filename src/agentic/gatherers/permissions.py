"""Permission enforcement (Malik).

The permissions config (config/permissions.yaml) is the trusted source of
the session's principal role and the role->department access map. The
environment config's per-department ``allowed_roles`` is a second,
independent grant. A role may read a department only when BOTH grant
access (AND semantics) — any disagreement fails closed to deny.

The mandatory, non-bypassable gate lives in spawn.spawn_gatherers; the
gatherer's own pre-read check here is the first (efficiency) layer.

Cloud-backed departments (gcs / drive) go through the same gate; their
containment is string-level (no symlinks/cwd), with backend-specific roots
(bucket prefix, folder parent-chain) enforced inside the cloud adapters.
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


def _path_within(root: Path, path: str) -> bool:
    """True if `path` resolves strictly inside the filesystem `root`.

    Rejects ``..`` escapes, absolute paths, both separator styles, empty/'.'
    paths, and symlinks that resolve outside the root.
    """
    if not path or not path.strip():
        return False

    raw = Path(path.replace("\\", "/"))
    if raw.is_absolute():
        return False

    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate != root


def _cloud_within(path: str) -> bool:
    """String-level containment for cloud keys (gcs relative paths / drive paths).

    GCS and Drive have no symlinks and no cwd, so containment reduces to
    rejecting empty/'.' paths, absolute keys, ``\\`` separators, and any
    ``..`` segment. The backend-specific root checks (bucket prefix for gcs,
    folder parent-chain for drive) are enforced inside the adapters before a
    GatheredFile ever reaches the spawn gate.
    """
    if not path or not path.strip():
        return False

    raw = path.replace("\\", "/")
    if raw.startswith("/"):
        return False

    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        return False
    if any(p == ".." for p in parts):
        return False
    return True


def check(path: str, requester_role: str, department: DepartmentConfig) -> bool:
    """True if `requester_role` may read `path` in this department.

    Three checks, all must pass:
    1. the role is on the department's allow-list,
    2. the path is non-empty and not '.', and
    3. the path stays strictly inside the department's data root —
       filesystem containment for local departments, string-level
       containment for cloud-backed ones.
    """
    if requester_role not in department.allowed_roles:
        return False

    if department.storage is not None:
        return _cloud_within(path)
    return _path_within(Path(department.path).resolve(), path)


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
