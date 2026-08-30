import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from agentic.contracts.config import AgentType, load_config
from agentic.contracts.messages import GatherRequest
from agentic.state_manager import manager_planning
from agentic.state_manager.manager_planning import build_plan_schema, plan_to_requests


def test_plan_schema_accepts_known_department():
    config = load_config("config/environment.example.yaml")
    Plan = build_plan_schema([d.name for d in config.departments])
    plan = Plan.model_validate_json(
        '{"tasks": [{"agent_key": "engineering", "task_prompt": "find the roadmap"}]}'
    )
    assert plan.tasks[0].task_prompt == "find the roadmap"


def test_plan_schema_rejects_unknown_department():
    config = load_config("config/environment.example.yaml")
    Plan = build_plan_schema([d.name for d in config.departments])
    with pytest.raises(ValidationError):
        Plan.model_validate_json(
            '{"tasks": [{"agent_key": "not-a-department", "task_prompt": "x"}]}'
        )


def test_plan_schema_requires_nonempty_departments():
    with pytest.raises(ValueError):
        build_plan_schema([])


def test_plan_to_requests():
    config = load_config("config/environment.example.yaml")
    Plan = build_plan_schema([d.name for d in config.departments])
    plan = Plan.model_validate_json(
        '{"tasks": [{"agent_key": "finance", "task_prompt": "find the q3 budget"}]}'
    )
    requests = plan_to_requests(plan, config, requester_role="admin")
    assert len(requests) == 1
    request = requests[0]
    assert isinstance(request, GatherRequest)
    assert request.department == "finance"
    assert request.query == "find the q3 budget"
    assert request.max_files == config.agent_types[AgentType.FILE_GATHERER].max_files
    assert request.requester_role == "admin"


async def test_run_planner_empty_response_raises_clearly(monkeypatch):
    """A keyless/blocked model call used to die as 'NoneType has no parts'."""
    config = load_config("config/environment.example.yaml")

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            yield SimpleNamespace(is_final_response=lambda: True, content=None)

    monkeypatch.setattr(manager_planning, "Runner", FakeRunner)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        await manager_planning.run_planner("q", config)


# ---- one gatherer per department ----

def _plan(config, *pairs):
    Plan = build_plan_schema([d.name for d in config.departments])
    return Plan.model_validate(
        {"tasks": [{"agent_key": k, "task_prompt": q} for k, q in pairs]}
    )


def test_duplicate_departments_collapse_to_one_request():
    """The planner routinely fills every slot with two departments twice."""
    config = load_config("config/environment.example.yaml")
    plan = _plan(
        config,
        ("engineering", "find the Q3 spend"),
        ("finance", "find the Q3 budget"),
        ("engineering", "find the Q4 risks"),
        ("finance", "find the forecast"),
    )
    requests = plan_to_requests(plan, config, requester_role="admin")

    assert [r.department for r in requests] == ["engineering", "finance"]
    assert len({r.request_id for r in requests}) == 2


def test_merged_tasks_keep_both_asks():
    """Dropping the duplicate would silently lose what it asked for."""
    config = load_config("config/environment.example.yaml")
    plan = _plan(
        config,
        ("engineering", "find the Q3 spend"),
        ("engineering", "find the Q4 risks"),
    )
    query = plan_to_requests(plan, config)[0].query

    assert "find the Q3 spend" in query
    assert "find the Q4 risks" in query


def test_dedupe_preserves_planner_order_and_drops_repeated_prompts():
    config = load_config("config/environment.example.yaml")
    plan = _plan(
        config,
        ("finance", "the budget"),
        ("engineering", "the roadmap"),
        ("finance", "the budget"),  # verbatim repeat, not a second ask
    )
    requests = plan_to_requests(plan, config)

    assert [r.department for r in requests] == ["finance", "engineering"]
    assert requests[0].query == "the budget"


def test_dedupe_leaves_distinct_departments_alone():
    config = load_config("config/environment.example.yaml")
    plan = _plan(config, ("engineering", "a"), ("finance", "b"), ("hr", "c"))
    requests = plan_to_requests(plan, config)

    assert [(r.department, r.query) for r in requests] == [
        ("engineering", "a"), ("finance", "b"), ("hr", "c")
    ]


async def test_planner_dedupes_before_applying_the_cap(monkeypatch):
    """Capping first would spend slots on duplicates, then drop a department
    that never got one. Three distinct departments must survive a cap of 3
    even when the model padded the list out with repeats."""
    config = load_config("config/environment.example.yaml")
    config.gatherers.max_gatherers = 3
    tasks = [
        {"agent_key": "engineering", "task_prompt": "a"},
        {"agent_key": "engineering", "task_prompt": "b"},
        {"agent_key": "finance", "task_prompt": "c"},
        {"agent_key": "finance", "task_prompt": "d"},
        {"agent_key": "hr", "task_prompt": "e"},
    ]

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            import json
            yield SimpleNamespace(
                is_final_response=lambda: True,
                content=SimpleNamespace(
                    parts=[SimpleNamespace(text=json.dumps({"tasks": tasks}))]
                ),
            )

    monkeypatch.setattr(manager_planning, "Runner", FakeRunner)
    plan = await manager_planning.run_planner("q", config)

    assert [t.agent_key for t in plan.tasks] == ["engineering", "finance", "hr"]
    assert plan.tasks[0].task_prompt == "a; b"
