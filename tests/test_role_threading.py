"""Authenticated-role threading (provisional — Malik).

The server passes a JWT-sourced role into manager.plan_and_gather; these
tests pin the contract that the override reaches every gate fail-closed:
admin keeps hr files, analyst has them stripped, an unknown role raises
before any gatherer runs. No API key, no ADK — planner and gatherer are
faked.
"""

from types import SimpleNamespace

import pytest

from agentic.contracts.config import ConfigError, load_config
from agentic.contracts.messages import GatheredFile, GatherResult
from agentic.gatherers import permissions
from agentic.state_manager import manager

CONFIG = "config/environment.example.yaml"


def _plan():
    return SimpleNamespace(
        tasks=[
            SimpleNamespace(agent_key="engineering", task_prompt="roadmaps"),
            SimpleNamespace(agent_key="hr", task_prompt="comp bands"),
        ]
    )


def _install_fakes(monkeypatch, files_by_dept):
    # Tests must not depend on an untracked local permissions.yaml.
    monkeypatch.setattr(
        permissions, "DEFAULT_PERMISSIONS_CONFIG", "config/permissions.example.yaml"
    )

    async def fake_planner(prompt, config):
        return _plan()

    async def fake_gather(request, config):
        return GatherResult(
            request_id=request.request_id,
            department=request.department,
            files=[
                GatheredFile(path=p, department=request.department, content="c")
                for p in files_by_dept.get(request.department, [])
            ],
        )

    monkeypatch.setattr(manager, "run_planner", fake_planner)
    monkeypatch.setattr("agentic.gatherers.spawn.permissions", permissions)
    monkeypatch.setattr("agentic.gatherers.gather.gather", fake_gather)


async def test_admin_override_keeps_hr_files(monkeypatch):
    config = load_config(CONFIG)
    _install_fakes(monkeypatch, {"engineering": ["roadmap.md"], "hr": ["comp-bands.md"]})

    results = await manager.plan_and_gather("q", config, requester_role="admin")

    by_dept = {r.department: r for r in results}
    assert [f.path for f in by_dept["hr"].files] == ["comp-bands.md"]
    assert by_dept["hr"].denied == []


async def test_analyst_override_strips_hr_files(monkeypatch):
    config = load_config(CONFIG)
    _install_fakes(monkeypatch, {"engineering": ["roadmap.md"], "hr": ["comp-bands.md"]})

    results = await manager.plan_and_gather("q", config, requester_role="analyst")

    by_dept = {r.department: r for r in results}
    assert by_dept["hr"].files == []
    assert by_dept["hr"].denied == ["comp-bands.md"]


async def test_unknown_role_raises_before_any_gather(monkeypatch):
    config = load_config(CONFIG)
    seen = []

    async def explode(request, config):
        seen.append(request.department)
        raise AssertionError("no gatherer may run for an unknown role")

    _install_fakes(monkeypatch, {})
    monkeypatch.setattr("agentic.gatherers.gather.gather", explode)

    with pytest.raises(ConfigError):
        await manager.plan_and_gather("q", config, requester_role="ghost")
    assert seen == []


async def test_on_result_receives_gate_stripped_results(monkeypatch):
    config = load_config(CONFIG)
    _install_fakes(monkeypatch, {"engineering": ["roadmap.md"], "hr": ["comp-bands.md"]})

    observed = {}

    def on_result(request, result):
        observed[request.department] = (len(result.files), list(result.denied))

    await manager.plan_and_gather("q", config, requester_role="analyst", on_result=on_result)

    assert observed["engineering"] == (1, [])
    assert observed["hr"] == (0, ["comp-bands.md"])


async def test_yaml_principal_still_used_without_override(monkeypatch):
    """No override -> behaviour identical to pre-auth pipeline runs."""
    config = load_config(CONFIG)
    _install_fakes(monkeypatch, {"engineering": ["roadmap.md"], "hr": ["comp-bands.md"]})
    perms = permissions.load_permissions_config()

    results = await manager.plan_and_gather("q", config)

    by_dept = {r.department: r for r in results}
    if perms.principal.role == "admin":
        assert len(by_dept["hr"].files) == 1
    else:
        assert by_dept["hr"].files == []
