import pytest
from pydantic import ValidationError

from agentic.contracts.config import AgentType, load_config
from agentic.contracts.messages import GatherRequest
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
