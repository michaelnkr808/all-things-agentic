"""Citation coverage: how well an answer is anchored to what was gathered.

The veto checker returns a verdict — approved or not — which is the right
output for a gate and a poor one for a reader. It says an answer passed
without saying what passing was close to. This scores the same evidence
numerically, with no model call, so the UI can show *why* an answer looks
solid or thin.

Three numbers, each a failure mode we have actually seen:

- **integrity** — of the [[dept/path]] citations in the prose, how many name
  a file that was really gathered. The first live veto of this project
  caught exactly this: a synthesizer citing [[finance/infra-spend-q3.md]]
  when the file was [[engineering/infra-spend-q3.md]]. An unresolved
  citation looks identical to a real one in rendered markdown, which is
  what makes it worth counting.
- **grounding** — what fraction of the answer's paragraphs cite anything at
  all. Catches the answer that opens with three paragraphs of confident
  synthesis and cites a file only at the end.
- **usage** — what fraction of gathered files the answer drew on. Gatherers
  over-gather by design, so this is context rather than a target; a low
  number next to a short answer means material went unused.

Everything here is a pure function over text that already exists. Nothing
is inferred about whether a claim is *true* — that stays the veto checker's
job, and this is deliberately not a substitute for it.
"""

from __future__ import annotations

import re

from agentic.contracts.messages import CompiledOutput, GatherResult

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")

#: Lines that are structure rather than claims, and so are not asked to cite.
_SKIP_LINE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s*$|\||```)")


def _prose(markdown: str) -> str:
    """The answer body: no frontmatter, no '# Answer', no '## Sources' list.

    The Sources section cites every gathered file by construction, so leaving
    it in would score every answer as perfectly grounded.
    """
    body = markdown
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :]
    sources_at = body.find("## Sources")
    if sources_at != -1:
        body = body[:sources_at]
    return re.sub(r"^#+\s*Answer\s*\n", "", body.strip()).strip()


def _paragraphs(prose: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n{2,}", prose) if b.strip()]
    return [b for b in blocks if not _SKIP_LINE.match(b)]


def gathered_keys(results: list[GatherResult]) -> set[str]:
    """Every file that survived the gate, as the `department/path` a citation
    would have to name to resolve."""
    return {f"{f.department}/{f.path}" for r in results for f in r.files}


def score(compiled: CompiledOutput, results: list[GatherResult]) -> dict:
    """Citation coverage for one compiled answer. No model call.

    Ratios are None rather than 0.0 when their denominator is zero, so the UI
    can say "no citations" instead of showing a confident 0% that reads like
    a failing grade on an answer that simply had nothing to cite.
    """
    prose = _prose(compiled.obsidian_markdown)
    available = gathered_keys(results)

    cited = list(dict.fromkeys(WIKILINK.findall(prose)))
    resolved = [c for c in cited if c in available]
    unresolved = [c for c in cited if c not in available]
    uncited = sorted(available - set(resolved))

    paragraphs = _paragraphs(prose)
    with_citation = [p for p in paragraphs if WIKILINK.search(p)]

    def ratio(part: int, whole: int) -> float | None:
        return round(part / whole, 3) if whole else None

    return {
        "cited": len(cited),
        "resolved": len(resolved),
        "unresolved": unresolved,
        "gathered": len(available),
        "uncited_files": uncited,
        "paragraphs": len(paragraphs),
        "paragraphs_cited": len(with_citation),
        "integrity": ratio(len(resolved), len(cited)),
        "grounding": ratio(len(with_citation), len(paragraphs)),
        "usage": ratio(len(resolved), len(available)),
    }
