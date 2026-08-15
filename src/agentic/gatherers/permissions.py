"""Permission enforcement (Malik).

Interface fixed so Michael's gather() can call it now.
"""

from __future__ import annotations

from agentic.contracts.config import DepartmentConfig


def check(path: str, requester_role: str, department: DepartmentConfig) -> bool:
    """True if `requester_role` may read `path` in this department. (Malik)"""
    raise NotImplementedError("Malik: permissions")
