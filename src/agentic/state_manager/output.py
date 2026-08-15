"""Output emitter (Michael).

Renders the CompiledOutput as Obsidian-flavored markdown ([[wikilinks]],
#tags, YAML frontmatter). We only borrow the *syntax* — the result is
viewed in our own visualizer (client/viz.py), not the Obsidian app.

Pure function, no LLM: takes the synthesized answer + sources and renders
the markdown. Testable today with hand-written inputs — does not wait on
Malik's synthesizer.

Target format:
    ---
    prompt: "<original prompt>"
    revision: 0
    departments: [engineering, finance]
    ---
    # Answer
    <answer body, with inline [[engineering/roadmap.md]] links where cited>

    ## Sources
    - [[engineering/roadmap.md]] #engineering
    - [[finance/q3-budget.csv]] #finance
"""

from __future__ import annotations

from agentic.contracts.messages import CompiledOutput, GatheredFile


def emit(prompt: str, answer_body: str, files: list[GatheredFile], revision: int = 0) -> CompiledOutput:
    """Render the final obsidian-flavored markdown. (Michael — TODO)"""
    raise NotImplementedError("Michael: output emitter")
