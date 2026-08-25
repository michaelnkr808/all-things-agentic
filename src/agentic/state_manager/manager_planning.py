"""Planner (Malik).

Cheap model, takes the user prompt, emits a Plan.

The Plan schema is built dynamically per-request so agent_key is a closed
Literal over the department names actually present in this EnvironmentConfig
— not a hardcoded list. If the model emits a name outside that set, pydantic
validation fails before anything downstream runs; there is no code path
where an unknown or wider-scoped target can slip through.
"""

from __future__ import annotations

from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, create_model

from agentic.contracts.config import AgentType, EnvironmentConfig
from agentic.contracts.messages import GatherRequest


class GatherTask(BaseModel):
    agent_key: str  # narrowed dynamically per-request, see build_plan_schema()
    task_prompt: str  # the specific ask for that department, e.g. "find the Q3 revenue file"


def build_plan_schema(department_names: list[str]) -> type[BaseModel]:
    """Dynamically builds a Plan model whose GatherTask.agent_key is a closed Literal."""
    if not department_names:
        raise ValueError("EnvironmentConfig has no departments configured.")

    AgentKey = Literal[tuple(department_names)]  # type: ignore[valid-type]

    ScopedGatherTask = create_model(
        "ScopedGatherTask",
        agent_key=(AgentKey, ...),
        task_prompt=(str, ...),
    )

    Plan = create_model(
        "Plan",
        tasks=(list[ScopedGatherTask], ...),
    )
    return Plan


def _department_catalog_text(config: EnvironmentConfig) -> str:
    lines = [f"- {d.name} (reads from {d.path})" for d in config.departments]
    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    """output_schema responses can arrive wrapped in ```json ... ``` fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return text


async def run_planner(prompt: str, config: EnvironmentConfig) -> BaseModel:
    """Returns an instance of the dynamically-built Plan model."""
    department_names = [d.name for d in config.departments]
    Plan = build_plan_schema(department_names)

    planner_agent = LlmAgent(
        name="planner",
        model=config.models.state_manager,
        instruction=(
            "You break a user request into gathering tasks, one per relevant "
            "department. Available departments and what each can access:\n"
            f"{_department_catalog_text(config)}\n\n"
            f"Propose at most {config.gatherers.max_gatherers} tasks. "
            "Only use department names from the list above — you cannot "
            "invent new ones or request access outside what's listed. "
            "For each task, write a specific, concrete task_prompt describing "
            "exactly what that department's gatherer should look for."
        ),
        output_schema=Plan,
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="planner", user_id="system", session_id=None
    )
    runner = Runner(agent=planner_agent, app_name="planner", session_service=session_service)

    result_text = ""
    async for event in runner.run_async(
        user_id="system",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        if event.is_final_response():
            if event.content is None or not event.content.parts:
                raise RuntimeError(
                    f"planner got no content back from {config.models.state_manager} "
                    f"— usually a missing GOOGLE_API_KEY, or the response was "
                    f"blocked/unavailable"
                )
            result_text = event.content.parts[0].text

    plan = Plan.model_validate_json(_strip_code_fences(result_text))

    # Enforce the cap even if the model ignored the instruction.
    plan.tasks = plan.tasks[: config.gatherers.max_gatherers]
    return plan


def plan_to_requests(
    plan: BaseModel,
    config: EnvironmentConfig,
    requester_role: str = "analyst",
) -> list[GatherRequest]:
    """Convert planner output into the GatherRequests that spawn_gatherers() fans out.

    The role comes from the trusted permissions config via
    manager.plan_and_gather; the default here is only a safe fallback for
    direct callers. spawn_gatherers re-pins the role to the permissions
    config regardless, so this value can never widen access.
    """
    agent_config = config.agent_types[AgentType.FILE_GATHERER]
    requests = []
    for i, task in enumerate(plan.tasks):
        requests.append(
            GatherRequest(
                request_id=f"{task.agent_key}-{i}",
                department=task.agent_key,
                query=task.task_prompt,
                keywords=[],
                max_files=agent_config.max_files,
                requester_role=requester_role,
                agent_type=AgentType.FILE_GATHERER,
            )
        )
    return requests
