"""Citation coverage scoring (Michael)."""

import pytest

from agentic import citations
from agentic.contracts.messages import CompiledOutput, GatheredFile, GatherResult


def _results(*pairs):
    by_dept: dict[str, list[GatheredFile]] = {}
    for dept, path in pairs:
        by_dept.setdefault(dept, []).append(
            GatheredFile(path=path, department=dept, content="x")
        )
    return [
        GatherResult(request_id=f"r{i}", department=dept, files=files)
        for i, (dept, files) in enumerate(by_dept.items())
    ]


def _compiled(body: str, sources=()):
    md = "---\nprompt: q\nrevision: 0\n---\n\n# Answer\n\n" + body
    if sources:
        md += "\n\n## Sources\n\n" + "\n".join(f"- [[{s}]] #x" for s in sources)
    return CompiledOutput(prompt="q", obsidian_markdown=md, sources=list(sources))


def test_a_fully_grounded_answer_scores_clean():
    out = _compiled(
        "Spend was $41,200 [[engineering/infra-spend-q3.md]].\n\n"
        "The budget was $50,000 [[finance/q3-budget.csv]]."
    )
    s = citations.score(out, _results(
        ("engineering", "infra-spend-q3.md"), ("finance", "q3-budget.csv")
    ))

    assert s["integrity"] == 1.0
    assert s["grounding"] == 1.0
    assert s["usage"] == 1.0
    assert s["unresolved"] == []


def test_the_real_hallucination_this_project_caught_is_counted():
    """The first live veto: the synthesizer cited the right filename under
    the wrong department. Rendered markdown makes that look identical to a
    real citation."""
    out = _compiled("Spend was $41,200 [[finance/infra-spend-q3.md]].")
    s = citations.score(out, _results(("engineering", "infra-spend-q3.md")))

    assert s["unresolved"] == ["finance/infra-spend-q3.md"]
    assert s["resolved"] == 0
    assert s["integrity"] == 0.0


def test_uncited_paragraphs_drag_grounding_down():
    out = _compiled(
        "Engineering overspent significantly this quarter.\n\n"
        "Leadership should expect further pressure in Q4.\n\n"
        "Actuals were $41,200 [[engineering/infra-spend-q3.md]]."
    )
    s = citations.score(out, _results(("engineering", "infra-spend-q3.md")))

    assert s["paragraphs"] == 3
    assert s["paragraphs_cited"] == 1
    assert s["grounding"] == pytest.approx(0.333, abs=0.001)
    assert s["integrity"] == 1.0  # what it did cite was real


def test_the_sources_section_is_not_counted_as_grounding():
    """Every gathered file appears under ## Sources by construction, so
    counting it would score every answer as perfectly grounded."""
    out = _compiled(
        "No citation anywhere in the prose.",
        sources=["engineering/roadmap.md", "finance/q3-budget.csv"],
    )
    s = citations.score(out, _results(
        ("engineering", "roadmap.md"), ("finance", "q3-budget.csv")
    ))

    assert s["cited"] == 0
    assert s["grounding"] == 0.0
    assert s["usage"] == 0.0


def test_unused_gathered_files_are_reported_not_penalised_as_errors():
    out = _compiled("Only one thing mattered [[engineering/roadmap.md]].")
    s = citations.score(out, _results(
        ("engineering", "roadmap.md"),
        ("engineering", "hiring-plan.md"),
        ("finance", "q3-budget.csv"),
    ))

    assert s["integrity"] == 1.0
    assert s["usage"] == pytest.approx(0.333, abs=0.001)
    assert s["uncited_files"] == ["engineering/hiring-plan.md", "finance/q3-budget.csv"]


def test_repeated_citations_count_once():
    out = _compiled(
        "First [[engineering/roadmap.md]].\n\nAgain [[engineering/roadmap.md]]."
    )
    s = citations.score(out, _results(("engineering", "roadmap.md")))

    assert s["cited"] == 1
    assert s["resolved"] == 1


def test_headings_and_bullets_are_not_asked_to_cite():
    out = _compiled(
        "## Findings\n\nSpend was high [[engineering/infra-spend-q3.md]]."
    )
    s = citations.score(out, _results(("engineering", "infra-spend-q3.md")))

    assert s["paragraphs"] == 1
    assert s["grounding"] == 1.0


def test_nothing_gathered_gives_null_ratios_not_a_confident_zero():
    """A 0% next to an answer that had nothing to cite reads as a failing
    grade rather than an absence of evidence."""
    s = citations.score(_compiled("No sources were available."), [])

    assert s["integrity"] is None
    assert s["usage"] is None
    assert s["grounding"] == 0.0
    assert s["gathered"] == 0


def test_a_heading_does_not_swallow_the_paragraph_under_it():
    """Caught on the deployed instance: models write `### Heading` and its
    prose as one block separated by a single newline. Filtering per block
    dropped the prose and its citations along with the heading, so an answer
    made entirely of cited paragraphs scored as having none."""
    out = _compiled(
        "### Q3 Spend\nEngineering came in under budget "
        "[[engineering/infra-spend-q3.md]].\n\n"
        "#### Headcount\n* Costs were $118,000 [[finance/headcount-costs.csv]]."
    )
    s = citations.score(out, _results(
        ("engineering", "infra-spend-q3.md"), ("finance", "headcount-costs.csv")
    ))

    assert s["paragraphs"] == 2
    assert s["paragraphs_cited"] == 2
    assert s["grounding"] == 1.0


def test_a_heading_only_block_is_not_counted_as_an_uncited_paragraph():
    out = _compiled("## Findings\n\n### Detail\n\nSpend was high [[engineering/roadmap.md]].")
    s = citations.score(out, _results(("engineering", "roadmap.md")))

    assert s["paragraphs"] == 1
    assert s["grounding"] == 1.0
