"""Visualizer tests — pure parsing plus rendered-file assertions. No browser,
no network: the page must be fully self-contained."""

from agentic.client import viz
from agentic.contracts.messages import GatheredFile, GatherResult, VetoVerdict
from agentic.state_manager import output

FILES = [
    GatheredFile(
        path="roadmap.md",
        department="engineering",
        content="Q3 roadmap: ship the auth service. Infra spend roughly $40k.",
        relevance_note="names the auth service as the Q3 deliverable",
    ),
    GatheredFile(
        path="q3-budget.csv",
        department="finance",
        content="line,amount\ninfra,41000\n",
        relevance_note="line 2 gives an infra budget of 41,000",
    ),
]


def _compiled():
    return output.emit(
        prompt="summarize Q3 engineering spend vs finance budget",
        answer_body=(
            "Spend is on track ([[engineering/roadmap.md]]) against the budget "
            "line ([[finance/q3-budget.csv]])."
        ),
        files=FILES,
    )


def _results():
    return [
        GatherResult(
            request_id="engineering-0", department="engineering", files=[FILES[0]]
        ),
        GatherResult(request_id="finance-1", department="finance", files=[FILES[1]]),
        GatherResult(request_id="hr-2", department="hr", denied=["comp-bands.md"]),
    ]


def test_parse_links_finds_targets_and_tags():
    targets, tags = viz.parse_links(_compiled().obsidian_markdown)
    assert "engineering/roadmap.md" in targets
    assert "finance/q3-budget.csv" in targets
    assert "engineering" in tags
    assert "finance" in tags


def test_parse_links_skips_frontmatter_fences_and_headings():
    markdown = (
        "---\n"
        'prompt: "frontmatter with #nottag and [[not/a-link.md]]"\n'
        "---\n\n"
        "# Answer\n\n"
        "real link [[engineering/roadmap.md]] #engineering\n\n"
        "```\nquoted [[fake/file.md]] #fake\n```\n"
    )
    targets, tags = viz.parse_links(markdown)
    assert targets == ["engineering/roadmap.md"]
    assert tags == ["engineering"]  # headings and fenced/frontmatter tags excluded


def test_parse_links_dedupes_preserving_order():
    targets, tags = viz.parse_links(
        "[[a/x.md]] #a then again [[a/x.md]] #a then [[b/y.md]] #b"
    )
    assert targets == ["a/x.md", "b/y.md"]
    assert tags == ["a", "b"]


def test_render_is_self_contained_with_all_nodes(tmp_path):
    out = viz.render_html(_compiled(), tmp_path / "graph.html", results=_results())
    page = out.read_text(encoding="utf-8")

    assert "<script src" not in page  # nothing fetched from the network
    assert "vis-network" in page  # vendored library actually inlined

    assert '"file:engineering/roadmap.md"' in page
    assert '"file:finance/q3-budget.csv"' in page
    assert '"dept:engineering"' in page
    assert "names the auth service as the Q3 deliverable" in page  # relevance note
    assert '"denied:hr/comp-bands.md"' in page  # permission-denied node present


def test_render_creates_parent_directories(tmp_path):
    out = viz.render_html(_compiled(), tmp_path / "nested" / "deep" / "graph.html")
    assert out.exists()


def test_render_marks_unapproved_verdict(tmp_path):
    vetoed = VetoVerdict(approved=False, reasons=["the $42k figure is uncited"])
    page = viz.render_html(
        _compiled(), tmp_path / "vetoed.html", verdict=vetoed
    ).read_text(encoding="utf-8")
    assert '"status": "unapproved"' in page
    assert "the $42k figure is uncited" in page

    approved = VetoVerdict(approved=True)
    page = viz.render_html(
        _compiled(), tmp_path / "approved.html", verdict=approved
    ).read_text(encoding="utf-8")
    assert '"status": "approved"' in page


def test_answer_panel_text_is_prose_not_markup():
    text = viz._answer_panel_text(_compiled().obsidian_markdown)
    assert "## Sources" not in text  # the graph replaces the sources list
    assert "# Answer" not in text
    assert "[[" not in text  # citations collapsed...
    assert "[roadmap.md]" in text  # ...to their filenames
    assert "Spend is on track" in text


def test_render_works_from_bare_compiled_output(tmp_path):
    """No results, no verdict — the renderer must not require the extras."""
    page = viz.render_html(_compiled(), tmp_path / "bare.html").read_text(
        encoding="utf-8"
    )
    assert '"status": null' in page
    assert '"file:engineering/roadmap.md"' in page


def test_embedded_content_cannot_close_the_script_tag(tmp_path):
    hostile = output.emit(
        prompt="q",
        answer_body="cites [[engineering/roadmap.md]]",
        files=[
            GatheredFile(
                path="roadmap.md",
                department="engineering",
                content="x",
                relevance_note="note with </script><script>alert(1)</script>",
            )
        ],
    )
    results = [
        GatherResult(
            request_id="r0",
            department="engineering",
            files=[
                GatheredFile(
                    path="roadmap.md",
                    department="engineering",
                    content="x",
                    relevance_note="note with </script><script>alert(1)</script>",
                )
            ],
        )
    ]
    page = viz.render_html(
        hostile, tmp_path / "hostile.html", results=results
    ).read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in page  # escaped to <\/script>...
