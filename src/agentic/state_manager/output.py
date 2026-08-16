"""Output emitter (Michael).

Renders the CompiledOutput as Obsidian-flavored markdown ([[wikilinks]],
#tags, YAML frontmatter). We only borrow the *syntax* — the result is
viewed in our own visualizer (client/viz.py), not the Obsidian app.

Pure function, no LLM: takes the synthesized answer + sources and renders
the markdown. Testable today with hand-written inputs

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

import yaml


def emit(
    prompt: str, answer_body: str, files: list[GatheredFile], revision: int = 0
) -> CompiledOutput:
    """Render the final obsidian-flavored markdown. (Michael)"""

    # Put together the list of sources, without duplicates
    sources = []
    for f in files:
        data = (f"{f.department}/{f.path}", f.department)
        sources.append(data)

    sources = list(dict.fromkeys(sources))

    # Put together the departments used
    departments = []
    for f in files:
        departments.append(f.department)

    departments = list(dict.fromkeys(departments))

    # Frontmatter
    front = {"prompt": prompt, "revision": revision, "departments": departments}
    dumped = yaml.safe_dump(front, sort_keys=False)
    frontmatter = f"---\n{dumped}---"

    # Answer written by state manager

    answer = f"# Answer\n\n{answer_body}"

    # Sources section

    lines = []
    for src, dept in sources:
        lines.append(f"- [[{src}]] #{dept}")

    sources_section = "## Sources\n\n" + f"\n".join(lines)

    # Combine Frontmatter, Answer and Sources
    final = f"{frontmatter}\n\n{answer}\n\n{sources_section}"

    return CompiledOutput(
        prompt=prompt,
        obsidian_markdown=final,
        sources=[src for src, dept in sources],
        departments_used=departments,
        revision=revision,
    )
