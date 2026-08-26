"""Synthesizer tests (Malik).

Only the failure guard for now — the happy path needs a live model.
"""

from types import SimpleNamespace

import pytest

from agentic.contracts.config import load_config
from agentic.state_manager import manager_synthesizer


async def test_run_synthesizer_empty_response_raises_clearly(monkeypatch):
    """A keyless/blocked model call used to die as 'NoneType has no parts'."""
    config = load_config("config/environment.example.yaml")

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            yield SimpleNamespace(is_final_response=lambda: True, content=None)

    monkeypatch.setattr(manager_synthesizer, "Runner", FakeRunner)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        await manager_synthesizer.run_synthesizer("q", "material", config.models)


def test_brief_heads_each_file_with_its_exact_citation_token():
    """The synthesizer copies this token verbatim rather than composing it.

    Composing 'department' + 'path' is where citations lose their prefix and
    become [[q3-budget.csv]] — a file node with no department edge in the graph
    and nothing for the veto checker to match against.
    """
    from agentic.contracts.messages import GatheredFile, GatherResult
    from agentic.state_manager.manager import _dump_results

    brief = _dump_results(
        [
            GatherResult(
                request_id="finance-0",
                department="finance",
                files=[
                    GatheredFile(
                        path="vendor-contracts/cloud-renewal.md",
                        department="finance",
                        content="renews Oct 1 at +7%",
                        relevance_note="names the renewal date",
                    )
                ],
                denied=["secret.md"],
            )
        ]
    )

    assert "### [[finance/vendor-contracts/cloud-renewal.md]]" in brief
    assert "names the renewal date" in brief
    assert "renews Oct 1 at +7%" in brief
    # A denied file is named so the answer can say it was withheld, but it
    # never gets a citation token — nothing may cite it.
    assert "[[finance/secret.md]]" not in brief
    assert "denied by permissions: secret.md" in brief
