import pytest

from agentic.contracts.config import ConfigError, DepartmentConfig, load_config
from agentic.gatherers.permissions import (
    Principal,
    PermissionsConfig,
    RoleAccess,
    allowed,
    check,
    load_permissions_config,
)


def _dept() -> DepartmentConfig:
    return DepartmentConfig(
        name="hr",
        path="data/departments/hr",
        allowed_roles=["admin"],
        file_globs=["**/*.md"],
    )


def _perms() -> PermissionsConfig:
    return PermissionsConfig(
        principal=Principal(role="analyst"),
        roles={
            "analyst": RoleAccess(departments=["engineering", "finance"]),
            "admin": RoleAccess(departments=["engineering", "finance", "hr"]),
        },
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


def test_denies_empty_and_dot_paths():
    assert check("", "admin", _dept()) is False
    assert check("   ", "admin", _dept()) is False
    assert check(".", "admin", _dept()) is False
    assert check("./", "admin", _dept()) is False


def test_denies_dotdot_resolving_to_root():
    assert check("foo/..", "admin", _dept()) is False


def test_denies_windows_style_traversal():
    assert check("..\\..\\finance\\q3-budget.csv", "admin", _dept()) is False


def test_denies_symlink_escape(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_text("secret")
    dept_dir = tmp_path / "dept"
    dept_dir.mkdir()
    (dept_dir / "ok.md").write_text("ok")
    (dept_dir / "evil.md").symlink_to(target / "secret.txt")
    dept = DepartmentConfig(
        name="d", path=str(dept_dir), allowed_roles=["admin"], file_globs=["**/*.md"]
    )
    assert check("ok.md", "admin", dept) is True
    assert check("evil.md", "admin", dept) is False


def test_allowed_requires_both_config_grants(tmp_path):
    env_config = load_config("config/environment.example.yaml")
    perms = _perms()

    eng = env_config.department("engineering")
    assert allowed("roadmap.md", "analyst", eng, perms) is True

    fin = env_config.department("finance")
    assert allowed("q3-budget.csv", "analyst", fin, perms) is True

    # permissions config does not grant analyst -> hr (env also denies it)
    hr = env_config.department("hr")
    assert allowed("comp-bands.md", "analyst", hr, perms) is False

    # env config denies engineer -> finance even though permissions grants it
    engineer_perms = PermissionsConfig(
        principal=Principal(role="engineer"),
        roles={"engineer": RoleAccess(departments=["finance"])},
    )
    assert allowed("q3-budget.csv", "engineer", fin, engineer_perms) is False

    # role not defined in the permissions config at all
    assert allowed("roadmap.md", "suspicious-role", eng, perms) is False


def test_load_permissions_config(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text(
        "principal:\n  role: analyst\n"
        "roles:\n  analyst:\n    departments: [engineering, finance]\n"
    )
    cfg = load_permissions_config(p)
    assert cfg.principal.role == "analyst"
    assert cfg.departments_for_role("analyst") == ["engineering", "finance"]
    assert cfg.departments_for_role("admin") == []


def test_load_permissions_config_missing_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_permissions_config(tmp_path / "nope.yaml")


def test_load_permissions_config_undefined_principal_role(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text(
        "principal:\n  role: admin\n"
        "roles:\n  analyst:\n    departments: [engineering]\n"
    )
    with pytest.raises(ConfigError):
        load_permissions_config(p)
