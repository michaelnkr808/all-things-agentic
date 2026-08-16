"""Synthesizer (Malik).

A separate agent from the planner: it condenses the raw gatherer output
into a tight brief for the final response-writing step. The state manager
layer (planner + synthesizer) owns the mid-tier model work.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agentic.contracts.config import ModelConfig


async def run_synthesizer(original_prompt: str, dumped_state: str, models: ModelConfig) -> str:
    synthesizer_agent = LlmAgent(
        name="synthesizer",
        model=models.state_manager,
        instruction=(
            "You are given the original request and the raw output of several "
            "gathering agents. Condense everything relevant into a tight brief "
            "for a final response-writing step. Drop anything irrelevant to the "
            "original request. Do not add information that isn't present in "
            "the gathered material."
        ),
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="synthesizer", user_id="system", session_id=None
    )
    runner = Runner(agent=synthesizer_agent, app_name="synthesizer", session_service=session_service)

    message = f"Original request:\n{original_prompt}\n\nGathered material:\n{dumped_state}"

    result_text = ""
    async for event in runner.run_async(
        user_id="system",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.is_final_response():
            result_text = event.content.parts[0].text

    return result_text
