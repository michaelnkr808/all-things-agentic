"""Our own graph visualizer (Michael) — renders the obsidian-flavored
markdown from state_manager/output.py. Obsidian-graph-view-like, but ours:
no Obsidian app involved.

Parse the CompiledOutput.obsidian_markdown:
- [[dept/file]] wikilinks  -> file nodes, edged to the answer node
- #dept tags               -> department cluster nodes
- frontmatter              -> title / revision badge

Emit a single self-contained HTML file (force-directed graph; embed the JS
so the demo works offline) and open it in the browser. This is the demo
centerpiece — answer node in the middle, departments as colored clusters,
source files hanging off them; click a file node to see its snippet.
"""

from __future__ import annotations

from pathlib import Path

from agentic.contracts.messages import CompiledOutput


def parse_links(markdown: str) -> tuple[list[str], list[str]]:
    """Return ([wikilink targets], [tags]) found in the markdown. (Michael — TODO)"""
    raise NotImplementedError("Michael: obsidian parsing")


def render_html(compiled: CompiledOutput, out_path: Path) -> Path:
    """Write the interactive graph page; return its path. (Michael — TODO)"""
    raise NotImplementedError("Michael: visualization")
