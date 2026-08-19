"""Veto retry loop (Michael).

The loop is the only logic pipeline.run() owns, so the manager and checker are
faked out: these tests assert on retry counts and on when revise() is paid for,
not on model behavior. No API key, no ADK.
"""

import pytest

from agentic import pipeline
from agentic.contracts.messages import CompiledOutput, GatherResult, VetoVerdict

CONFIG = "config/environment.example.yaml"


@pytest.fixture
def fleet(monkeypatch):
    """Installs fakes; returns a call counter the tests assert against."""
    calls = {"revise": 0, "check": 0}

    def install(verdicts: list[bool]):
        async def plan_and_gather(prompt, config):
            return [
                GatherResult(request_id="r0", department="finance"),
                GatherResult(request_id="r1", department="engineering"),
            ]

        async def synthesize(prompt, results, config):
            return CompiledOutput(prompt=prompt, obsidian_markdown="v0", revision=0)

        async def revise(prompt, previous, verdict, results, config):
            calls["revise"] += 1
            return CompiledOutput(
                prompt=prompt,
                obsidian_markdown=f"v{previous.revision + 1}",
                revision=previous.revision + 1,
            )

        async def check(prompt, compiled, config, results):
            approved = verdicts[calls["check"]]
            calls["check"] += 1
            return VetoVerdict(approved=approved, reasons=["fake reason"])

        monkeypatch.setattr(pipeline.manager, "plan_and_gather", plan_and_gather)
        monkeypatch.setattr(pipeline.manager, "synthesize", synthesize)
        monkeypatch.setattr(pipeline.manager, "revise", revise)
        monkeypatch.setattr(pipeline.checker, "check", check)
        return calls

    return install


async def test_approval_on_first_check_never_revises(fleet):
    calls = fleet([True])
    result = await pipeline.run("q", config_path=CONFIG, out_path=None)
    assert result.verdict.approved is True
    assert result.attempts == 1
    assert calls["revise"] == 0
    assert result.compiled.revision == 0


async def test_veto_then_approval_revises_once(fleet):
    calls = fleet([False, True])
    result = await pipeline.run("q", config_path=CONFIG, out_path=None)
    assert result.verdict.approved is True
    assert result.attempts == 2
    assert calls["revise"] == 1
    assert result.compiled.revision == 1


async def test_exhausted_budget_ships_unapproved(fleet):
    fleet([False, False, False])
    result = await pipeline.run("q", config_path=CONFIG, out_path=None)
    assert result.verdict.approved is False
    assert result.attempts == 3  # max_retries=2 -> three checks


async def test_final_veto_does_not_pay_for_an_unreviewed_revision(fleet):
    """The last check has no successor, so revising after it is wasted spend."""
    calls = fleet([False, False, False])
    await pipeline.run("q", config_path=CONFIG, out_path=None)
    assert calls["check"] == 3
    assert calls["revise"] == 2


async def test_renders_when_out_path_is_given(fleet, tmp_path, monkeypatch):
    fleet([True])
    written = {}

    def fake_render(compiled, out_path):
        written["path"] = out_path
        out_path.write_text("<html></html>")
        return out_path

    monkeypatch.setattr(pipeline.viz, "render_html", fake_render)
    out = tmp_path / "nested" / "graph.html"
    await pipeline.run("q", config_path=CONFIG, out_path=out)
    assert written["path"] == out
    assert out.exists()  # parent directory was created for us


async def test_skips_rendering_when_out_path_is_none(fleet, monkeypatch):
    """viz.py is unimplemented until section 3 — the pipeline must run without it."""
    fleet([True])

    def explode(compiled, out_path):
        raise AssertionError("render_html must not be called when out_path is None")

    monkeypatch.setattr(pipeline.viz, "render_html", explode)
    result = await pipeline.run("q", config_path=CONFIG, out_path=None)
    assert result.verdict.approved is True
