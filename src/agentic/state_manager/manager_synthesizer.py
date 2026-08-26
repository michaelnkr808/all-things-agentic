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
from agentic.retry import with_retry


async def run_synthesizer(original_prompt: str, dumped_state: str, models: ModelConfig) -> str:
    """Condense the gathered material into a brief, retrying transient failures.

    Everything upstream has already been paid for by the time this runs — the
    plan, the fan-out, every file read and assessed. Losing all of it to one
    503 is the most expensive failure in the pipeline, so the call goes
    through the shared retry policy (see agentic/retry.py).
    """
    return await with_retry(
        lambda: _synthesize_once(original_prompt, dumped_state, models),
        what="synthesizer",
    )


async def _synthesize_once(
    original_prompt: str, dumped_state: str, models: ModelConfig
) -> str:
    synthesizer_agent = LlmAgent(
        name="synthesizer",
        model=models.state_manager,
        instruction=(
            "You are given the original request and the raw output of several "
            "gathering agents. Condense everything relevant into a tight brief "
            "for a final response-writing step. Drop anything irrelevant to the "
            "original request. Do not add information that isn't present in "
            "the gathered material.\n\n"
            "Cite your sources inline. Every file you were given is headed by "
            "a [[department/path]] token; place that exact token in your text "
            "immediately after the specific claim it supports, copied verbatim "
            "from the heading. Cite the individual claim, not the paragraph: a "
            "figure, date, name or total needs the citation that backs it. "
            "Connective prose and general framing do not need one.\n\n"
            "Never invent a citation, never cite a file you were not given, "
            "and never cite a file listed as denied by permissions — if the "
            "material needed to answer part of the request was withheld, say "
            "so plainly instead. Do not add a sources list at the end; that "
            "section is generated for you."
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
            if event.content is None or not event.content.parts:
                raise RuntimeError(
                    f"synthesizer got no content back from {models.state_manager} "
                    f"— usually a missing GOOGLE_API_KEY, or the response was "
                    f"blocked/unavailable"
                )
            result_text = event.content.parts[0].text

    return result_text
