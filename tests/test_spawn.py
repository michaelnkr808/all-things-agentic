from agentic.contracts.config import load_config
from agentic.contracts.messages import GatherRequest, GatherResult
from agentic.gatherers import spawn


async def test_spawn_gatherers_fans_out(monkeypatch):
    config = load_config("config/environment.example.yaml")

    async def fake_gather(request, config):
        return GatherResult(request_id=request.request_id, department=request.department)

    monkeypatch.setattr("agentic.gatherers.gather.gather", fake_gather)

    requests = [
        GatherRequest(request_id="1", department="engineering", query="x"),
        GatherRequest(request_id="2", department="finance", query="y"),
        GatherRequest(request_id="3", department="hr", query="z"),
    ]
    results = await spawn.spawn_gatherers(requests, config)
    assert {r.department for r in results} == {"engineering", "finance", "hr"}
    assert len(results) == 3
