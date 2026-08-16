from agentic.contracts.config import DepartmentConfig
from agentic.gatherers.permissions import check


def _dept() -> DepartmentConfig:
    return DepartmentConfig(
        name="hr",
        path="data/departments/hr",
        allowed_roles=["admin"],
        file_globs=["**/*.md"],
    )


def test_allows_allowed_role():
    assert check("comp-bands.md", "admin", _dept()) is True


def test_denies_unknown_role():
    assert check("comp-bands.md", "analyst", _dept()) is False


def test_denies_empty_allowlist():
    dept = _dept()
    dept.allowed_roles = []
    assert check("comp-bands.md", "admin", dept) is False


def test_denies_parent_traversal():
    assert check("../engineering/roadmap.md", "admin", _dept()) is False


def test_denies_absolute_path():
    assert check("/etc/passwd", "admin", _dept()) is False
