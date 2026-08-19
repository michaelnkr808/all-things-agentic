"""Veto checker (Michael).

Offline tests cover the deterministic parts: the config summary, source
material assembly, and the fail-closed guard. The live verdict cases hit the
API and skip without ANTHROPIC_API_KEY, so the suite stays green in CI.
"""

import os
import types

import pytest

from agentic.contracts.config import load_config
from agentic.contracts.messages import (
    CompiledOutput,
    GatheredFile,
    GatherResult,
    VetoVerdict,
)
from agentic.veto import checker

CONFIG = "config/environment.example.yaml"

needs_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


def _file(dept, path, body):
    return GatheredFile(path=path, department=dept, content=body)


def _compiled(markdown="answer", sources=None):
    return CompiledOutput(
        prompt="q",
        obsidian_markdown=markdown,
        sources=sources or [],
        departments_used=["finance"],
        revision=0,
    )


# --- config_summary ---------------------------------------------------------


def test_config_summary_lists_every_department_and_its_roles():
    summary = checker.config_summary(load_config(CONFIG))
    assert "engineering: analyst, engineer, admin" in summary
    assert "finance: analyst, admin" in summary
    assert "hr: admin" in summary


def test_config_summary_omits_filesystem_paths():
    """Paths are noise to the checker — answers cite department names."""
    assert "data/departments" not in checker.config_summary(load_config(CONFIG))


# --- source_material --------------------------------------------------------


def test_cited_files_are_dumped_in_full():
    results = [
        GatherResult(
            request_id="f0",
            department="finance",
            files=[_file("finance", "q3.md", "TOTAL WAS 318400")],
        )
    ]
    text = checker.source_material(_compiled(sources=["finance/q3.md"]), results)
    assert "TOTAL WAS 318400" in text
    assert "--- [[finance/q3.md]] ---" in text


def test_uncited_files_are_named_but_not_dumped():
    """Gatherers over-gather; naming uncited files is enough, dumping is waste."""
    results = [
        GatherResult(
            request_id="e0",
            department="engineering",
            files=[_file("engineering", "notes.md", "SECRET BODY TEXT")],
        )
    ]
    text = checker.source_material(_compiled(sources=[]), results)
    assert "SECRET BODY TEXT" not in text
    assert "- engineering/notes.md" in text


def test_duplicate_files_are_emitted_once():
    """Two planner tasks on one department, or overlapping globs, duplicate files."""
    dup = [_file("finance", "q3.md", "BODY"), _file("finance", "other.md", "X")]
    results = [
        GatherResult(request_id="f0", department="finance", files=list(dup)),
        GatherResult(request_id="f1", department="finance", files=list(dup)),
    ]
    text = checker.source_material(_compiled(sources=["finance/q3.md"]), results)
    assert text.count("--- [[finance/q3.md]] ---") == 1
    assert text.count("- finance/other.md") == 1


def test_long_files_are_truncated_with_a_marker():
    body = "x" * (checker.MAX_SOURCE_CHARS + 500)
    results = [
        GatherResult(
            request_id="f0",
            department="finance",
            files=[_file("finance", "big.md", body)],
        )
    ]
    text = checker.source_material(_compiled(sources=["finance/big.md"]), results)
    assert "[... truncated ...]" in text
    assert len(text) < len(body) + 200


def test_no_cited_files_says_so_explicitly():
    text = checker.source_material(_compiled(sources=["finance/ghost.md"]), [])
    assert "no cited files" in text


# --- fail-closed guard ------------------------------------------------------


def _fake_response(stop_reason, parsed, stop_details=None):
    return types.SimpleNamespace(
        stop_reason=stop_reason, parsed_output=parsed, stop_details=stop_details
    )


def _install_fake_client(monkeypatch, response):
    async def fake_parse(**kwargs):
        return response

    monkeypatch.setattr(
        checker,
        "client",
        types.SimpleNamespace(messages=types.SimpleNamespace(parse=fake_parse)),
    )


@pytest.mark.parametrize(
    "stop_reason,stop_details",
    [
        ("refusal", types.SimpleNamespace(type="refusal")),
        ("max_tokens", None),
        ("model_context_window_exceeded", None),
        ("end_turn", None),  # parsed_output None despite a clean stop
    ],
)
async def test_incomplete_response_vetoes_rather_than_returning_none(
    monkeypatch, stop_reason, stop_details
):
    """An unreviewed answer must never read as approved."""
    _install_fake_client(monkeypatch, _fake_response(stop_reason, None, stop_details))
    verdict = await checker.check("q", _compiled(), load_config(CONFIG), [])
    assert verdict.approved is False
    assert stop_reason in verdict.reasons[0]
    assert verdict.revision_notes


async def test_complete_response_is_returned_unchanged(monkeypatch):
    expected = VetoVerdict(approved=True, reasons=["fine"], revision_notes="")
    _install_fake_client(monkeypatch, _fake_response("end_turn", expected))
    verdict = await checker.check("q", _compiled(), load_config(CONFIG), [])
    assert verdict is expected


async def test_source_material_reaches_the_model(monkeypatch):
    """Regression guard: the whole point of results is that the model sees it."""
    captured = {}

    async def fake_parse(**kwargs):
        captured.update(kwargs)
        return _fake_response("end_turn", VetoVerdict(approved=True))

    monkeypatch.setattr(
        checker,
        "client",
        types.SimpleNamespace(messages=types.SimpleNamespace(parse=fake_parse)),
    )
    results = [
        GatherResult(
            request_id="f0",
            department="finance",
            files=[_file("finance", "q3.md", "DISTINCTIVE SOURCE BODY")],
        )
    ]
    await checker.check(
        "q", _compiled(sources=["finance/q3.md"]), load_config(CONFIG), results
    )
    content = captured["messages"][0]["content"]
    assert "DISTINCTIVE SOURCE BODY" in content
    assert "SOURCE MATERIAL:" in content
    assert "ENVIRONMENT CONFIG:" in content


# --- live verdicts ----------------------------------------------------------

Q3_SUMMARY = """# Q3 Financial Summary

Total operating expenses for Q3 were $318,400, up from $291,000 in Q2.
Operating expenses include vendor and infrastructure spend, which is not
broken out separately in this summary.
"""

LIVE_RESULTS = [
    GatherResult(
        request_id="finance-0",
        department="finance",
        files=[_file("finance", "q3-summary.md", Q3_SUMMARY)],
    )
]

PROMPT = "What did we spend on cloud infrastructure in Q3, and who approved it?"

FABRICATED = """# Answer

We spent $42,300 on cloud infrastructure in Q3, approved by Dana Whitfield.

## Sources

- [[finance/q3-summary.md]] #finance
"""

UNLISTED_DEPARTMENT = """# Answer

Q3 operating expenses were $318,400.

## Sources

- [[finance/q3-summary.md]] #finance
- [[legal/vendor-contracts.md]] #legal
"""

HONEST = """# Answer

Q3 operating expenses totalled $318,400, up from $291,000 in Q2. Infrastructure
spend is not broken out separately, so a cloud-specific total is unavailable,
and no approval record appears in the gathered files.

## Sources

- [[finance/q3-summary.md]] #finance
"""


@needs_api_key
async def test_live_vetoes_claims_absent_from_the_source():
    compiled = _compiled(FABRICATED, sources=["finance/q3-summary.md"])
    verdict = await checker.check(
        PROMPT, compiled, load_config(CONFIG), LIVE_RESULTS
    )
    assert verdict.approved is False
    assert verdict.revision_notes
    assert "42,300" in verdict.revision_notes or "Whitfield" in verdict.revision_notes


@needs_api_key
async def test_live_vetoes_a_department_not_in_the_config():
    compiled = _compiled(
        UNLISTED_DEPARTMENT,
        sources=["finance/q3-summary.md", "legal/vendor-contracts.md"],
    )
    verdict = await checker.check(
        PROMPT, compiled, load_config(CONFIG), LIVE_RESULTS
    )
    assert verdict.approved is False
    assert any("legal" in r.lower() for r in verdict.reasons)


@needs_api_key
async def test_live_approves_a_narrow_honest_answer():
    """The failure mode that matters: over-vetoing burns the whole retry budget."""
    compiled = _compiled(HONEST, sources=["finance/q3-summary.md"])
    verdict = await checker.check(
        PROMPT, compiled, load_config(CONFIG), LIVE_RESULTS
    )
    assert verdict.approved is True
    assert verdict.revision_notes == ""
