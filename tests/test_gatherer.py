from agentic.contracts.config import load_config
from agentic.contracts.messages import GatherRequest
from agentic.gatherers.gatherer import gather_files


def test_config_loads_from_example():
    config = load_config("config/environment.example.yaml")
    assert {d.name for d in config.departments} == {"engineering", "finance", "hr"}


async def test_gatherer_reads_engineering():
    config = load_config("config/environment.example.yaml")
    request = GatherRequest(request_id="r1", department="engineering", query="roadmap")
    result = await gather_files(request, config)
    # Asserts the invariant, not the fixture inventory: adding demo data to
    # data/departments/ must not break the gatherer's tests.
    assert "roadmap.md" in {f.path for f in result.files}
    assert all(f.department == "engineering" for f in result.files)
    assert result.denied == []


async def test_gatherer_denies_hr_for_analyst():
    config = load_config("config/environment.example.yaml")
    request = GatherRequest(request_id="r2", department="hr", query="compensation")
    result = await gather_files(request, config)
    assert result.files == []
    assert "comp-bands.md" in result.denied


async def test_gatherer_reads_finance_for_analyst():
    config = load_config("config/environment.example.yaml")
    request = GatherRequest(request_id="r3", department="finance", query="q3 budget")
    result = await gather_files(request, config)
    assert "q3-budget.csv" in {f.path for f in result.files}
    assert all(f.department == "finance" for f in result.files)
    assert result.denied == []


async def test_gatherer_cannot_cross_departments():
    config = load_config("config/environment.example.yaml")
    request = GatherRequest(
        request_id="r4",
        department="engineering",
        query="compensation bands q3 budget marketing spend",
    )
    result = await gather_files(request, config)
    assert result.files, "engineering should still return its own files"
    assert all(f.department == "engineering" for f in result.files)
    # The query deliberately begs for HR and finance material; none of it may
    # appear, however relevant the wording makes it look.
    joined = " ".join(f.content for f in result.files).lower()
    assert "compensation" not in joined
    assert "budgeted_usd" not in joined
    assert "CONFIDENTIAL".lower() not in joined
