"""Permission enforcement (Malik).

Interface fixed so Michael's gather() can call it now.
"""

from __future__ import annotations

from pathlib import Path

from agentic.contracts.config import DepartmentConfig


def check(path: str, requester_role: str, department: DepartmentConfig) -> bool:
    """True if `requester_role` may read `path` in this department.

    Two checks, both must pass:
    1. the role is on the department's allow-list, and
    2. the path resolves to a location inside the department's data dir
       (rejects ``..`` escapes and absolute paths).
    """
    if requester_role not in department.allowed_roles:
        return False

    raw = Path(path)
    if raw.is_absolute():
        return False

    root = Path(department.path).resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
