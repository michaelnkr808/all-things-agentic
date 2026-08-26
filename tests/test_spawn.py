from agentic.contracts.config import load_config
from agentic.contracts.messages import GatheredFile, GatherRequest, GatherResult
from agentic.gatherers import spawn
from agentic.gatherers.permissions import Principal, PermissionsConfig, RoleAccess


def _perms() -> PermissionsConfig:
    return PermissionsConfig(
        principal=Principal(role="analyst"),
        roles={
            "analyst": RoleAccess(departments=["engineering", "finance"]),
            "admin": RoleAccess(departments=["engineering", "finance", "hr"]),
        },
    )


async def test_spawn_gatherers_fans_out(monkeypatch):
    config = load_config("config/environment.example.yaml")

    async def fake_gather(request, config, permissions_cfg=None, on_progress=None):
        return GatherResult(request_id=request.request_id, department=request.department)

    monkeypatch.setattr("agentic.gatherers.gather.gather", fake_gather)

    requests = [
        GatherRequest(request_id="1", department="engineering", query="x"),
        GatherRequest(request_id="2", department="finance", query="y"),
        GatherRequest(request_id="3", department="hr", query="z"),
    ]
    results = await spawn.spawn_gatherers(requests, config, permissions_cfg=_perms())
    assert {r.department for r in results} == {"engineering", "finance", "hr"}
    assert len(results) == 3


async def test_spawn_gate_strips_illegal_files(monkeypatch):
    config = load_config("config/environment.example.yaml")

    async def rogue_gatherer(request, config, permissions_cfg=None, on_progress=None):
        return GatherResult(
            request_id=request.request_id,
            department="engineering",
            files=[
                GatheredFile(path="roadmap.md", department="engineering", content="ok"),
                GatheredFile(path="../finance/q3-budget.csv", department="engineering", content="budget"),
                GatheredFile(path="comp-bands.md", department="hr", content="bands"),
            ],
        )

    monkeypatch.setattr("agentic.gatherers.gather.gather", rogue_gatherer)

    requests = [
        GatherRequest(request_id="1", department="engineering", query="x", requester_role="analyst")
    ]
    results = await spawn.spawn_gatherers(requests, config, permissions_cfg=_perms())
    result = results[0]
    assert [f.path for f in result.files] == ["roadmap.md"]
    assert set(result.denied) == {"../finance/q3-budget.csv", "comp-bands.md"}


async def test_spawn_gate_denies_on_role_mismatch(monkeypatch):
    config = load_config("config/environment.example.yaml")

    async def fake_gatherer(request, config, permissions_cfg=None, on_progress=None):
        return GatherResult(
            request_id=request.request_id,
            department="engineering",
            files=[GatheredFile(path="roadmap.md", department="engineering", content="ok")],
        )

    monkeypatch.setattr("agentic.gatherers.gather.gather", fake_gatherer)

    requests = [
        GatherRequest(request_id="1", department="engineering", query="x", requester_role="admin")
    ]
    results = await spawn.spawn_gatherers(requests, config, permissions_cfg=_perms())
    result = results[0]
    assert result.files == []
    assert result.denied == ["roadmap.md"]


async def test_spawn_gate_denies_permissions_config_denied_department(monkeypatch):
    config = load_config("config/environment.example.yaml")

    async def hr_gatherer(request, config, permissions_cfg=None, on_progress=None):
        return GatherResult(
            request_id=request.request_id,
            department="hr",
            files=[GatheredFile(path="comp-bands.md", department="hr", content="bands")],
        )

    monkeypatch.setattr("agentic.gatherers.gather.gather", hr_gatherer)

    requests = [
        GatherRequest(request_id="1", department="hr", query="comp", requester_role="analyst")
    ]
    results = await spawn.spawn_gatherers(requests, config, permissions_cfg=_perms())
    result = results[0]
    assert result.files == []
    assert result.denied == ["comp-bands.md"]
