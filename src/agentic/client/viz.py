"""Our own graph visualizer — renders the obsidian-flavored markdown from
state_manager/output.py as an interactive force-directed graph. Obsidian-
graph-view-like, but ours: no Obsidian app involved.

The output is a single self-contained HTML file: the vis-network library is
vendored and inlined, the graph data is embedded as JSON, and nothing is
fetched from the network — the demo works offline.

Node vocabulary:
    answer      the compiled answer, center of the graph
    department  one per #tag / source prefix, a colored cluster hub
    file        one per [[dept/path]] wikilink, colored by its department;
                click shows the gatherer's relevance note
    denied      files the permission gate stripped — drawn locked and gray,
                so the permissions story is visible in the graph itself
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from agentic.contracts.messages import CompiledOutput, GatherResult, VetoVerdict

VENDOR_JS = Path(__file__).parent / "vendor" / "vis-network.min.js"

_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
# A tag is '#word' not preceded by a word character or another '#', so
# markdown headings ('# Answer', '## Sources') never count.
_TAG = re.compile(r"(?<![\w#])#(\w[\w-]*)")

# Dark-background palette, one color per department, cycled.
_PALETTE = ["#7aa2f7", "#f7768e", "#9ece6a", "#e0af68", "#bb9af7", "#2ac3de", "#ff9e64"]

_ANSWER_COLOR = {"background": "#e6e6fa", "border": "#a9a1e1"}
_DENIED_COLOR = {"background": "#3a3a44", "border": "#565662"}


def _strip_frontmatter(markdown: str) -> str:
    """Drop the YAML frontmatter block; the prompt inside it is not content."""
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return markdown[end + 4 :]
    return markdown


def parse_links(markdown: str) -> tuple[list[str], list[str]]:
    """Return ([wikilink targets], [tags]) found in the markdown.

    Frontmatter and fenced code blocks are skipped: a [[link]] quoted inside
    a code sample is not a citation. Order-preserving, de-duplicated.
    """
    text = _FENCED_CODE.sub("", _strip_frontmatter(markdown))
    targets = list(dict.fromkeys(m.group(1).strip() for m in _WIKILINK.finditer(text)))
    tags = list(dict.fromkeys(m.group(1) for m in _TAG.finditer(text)))
    return targets, tags


def _department_of(target: str) -> str | None:
    return target.split("/", 1)[0] if "/" in target else None


def _answer_panel_text(markdown: str) -> str:
    """The answer body as a human reads it in the click panel.

    Frontmatter, the '# Answer' heading, and the '## Sources' section are all
    redundant on this page — the header shows the prompt and the graph *is*
    the sources list — so only the prose remains, with each [[dept/path]]
    citation collapsed to its filename.
    """
    body = _strip_frontmatter(markdown)
    sources_at = body.find("## Sources")
    if sources_at != -1:
        body = body[:sources_at]
    body = re.sub(r"^#+ Answer\s*\n", "", body.strip())
    return _WIKILINK.sub(lambda m: "[" + m.group(1).split("/")[-1] + "]", body).strip()


def _build_graph(
    compiled: CompiledOutput,
    results: list[GatherResult] | None,
    verdict: VetoVerdict | None,
) -> dict:
    targets, tags = parse_links(compiled.obsidian_markdown)

    notes: dict[str, str] = {}
    denied: list[tuple[str, str]] = []
    if results:
        for r in results:
            for f in r.files:
                notes[f"{f.department}/{f.path}"] = f.relevance_note
            for path in r.denied:
                denied.append((r.department, path))
    denied = list(dict.fromkeys(denied))

    departments = list(
        dict.fromkeys(
            tags
            + [d for d in (_department_of(t) for t in targets) if d]
            + [d for d, _ in denied]
            + compiled.departments_used
        )
    )
    color_of = {d: _PALETTE[i % len(_PALETTE)] for i, d in enumerate(departments)}

    nodes: list[dict] = [
        {
            "id": "answer",
            "label": "Answer",
            "kind": "answer",
            "shape": "dot",
            "size": 28,
            "color": _ANSWER_COLOR,
            "full_label": compiled.prompt,
            "detail": _answer_panel_text(compiled.obsidian_markdown),
        }
    ]
    edges: list[dict] = []

    for dept in departments:
        nodes.append(
            {
                "id": f"dept:{dept}",
                "label": dept,
                "kind": "department",
                "shape": "hexagon",
                "size": 18,
                "color": {"background": color_of[dept], "border": color_of[dept]},
                "detail": f"department cluster — files colored {color_of[dept]}",
            }
        )

    for target in targets:
        dept = _department_of(target)
        color = color_of.get(dept, "#8b8bb0")
        nodes.append(
            {
                "id": f"file:{target}",
                "label": target.split("/", 1)[1] if dept else target,
                "kind": "file",
                "shape": "dot",
                "size": 11,
                "color": {"background": color, "border": color},
                "full_label": target,
                "detail": notes.get(target, "")
                or "(no relevance note — gatherer results not provided)",
            }
        )
        edges.append({"from": "answer", "to": f"file:{target}"})
        if dept:
            edges.append({"from": f"file:{target}", "to": f"dept:{dept}"})

    for dept, path in denied:
        node_id = f"denied:{dept}/{path}"
        nodes.append(
            {
                "id": node_id,
                "label": f"{path} 🔒",
                "kind": "denied",
                "shape": "dot",
                "size": 11,
                "color": _DENIED_COLOR,
                "shapeProperties": {"borderDashes": [4, 4]},
                "font": {"color": "#6a6a78"},
                "full_label": f"{dept}/{path}",
                "detail": "stripped by the permission gate — the principal role "
                "may not read this file",
            }
        )
        edges.append({"from": node_id, "to": f"dept:{dept}", "dashes": True})

    status = None
    reasons: list[str] = []
    if verdict is not None:
        status = "approved" if verdict.approved else "unapproved"
        reasons = verdict.reasons

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "title": compiled.prompt,
            "revision": compiled.revision,
            "status": status,
            "reasons": reasons,
        },
    }


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  html, body { margin: 0; height: 100%; background: #16161e; color: #c9c9d9;
               font-family: system-ui, sans-serif; overflow: hidden; }
  #graph { position: absolute; inset: 0; }
  header { position: fixed; top: 0; left: 0; right: 0; z-index: 10;
           padding: 10px 16px; display: flex; align-items: center; gap: 10px;
           background: linear-gradient(#16161ee6, #16161e00); pointer-events: none; }
  header h1 { font-size: 15px; font-weight: 600; margin: 0; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px;
           background: #26263a; color: #8b8bb0; flex-shrink: 0; }
  .badge.approved { background: #1c3a2a; color: #7dd8a0; }
  .badge.unapproved { background: #4a1d1d; color: #f7768e; }
  #banner { position: fixed; top: 40px; left: 16px; right: 16px; z-index: 10;
            padding: 8px 12px; border-radius: 6px; background: #4a1d1d;
            color: #f7b1bd; font-size: 13px; }
  #panel { position: fixed; top: 0; right: 0; bottom: 0; width: 340px; z-index: 20;
           background: #1d1d29; border-left: 1px solid #2c2c3d; padding: 16px;
           overflow-y: auto; box-sizing: border-box; }
  #panel-kind { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
                color: #8b8bb0; margin: 0 0 4px; }
  #panel-title { font-size: 14px; font-weight: 600; margin: 0 0 12px;
                 word-break: break-all; }
  #panel-body { font-size: 13px; line-height: 1.5; white-space: pre-wrap;
                word-break: break-word; color: #b3b3c6; margin: 0;
                font-family: inherit; }
  #hint { position: fixed; bottom: 12px; left: 16px; z-index: 10; font-size: 12px;
          color: #565670; }
</style>
</head>
<body>
<script>__VIS_JS__</script>
<header>
  <h1 id="title"></h1>
  <span class="badge" id="rev"></span>
  <span class="badge" id="status" hidden></span>
</header>
<div id="banner" hidden></div>
<div id="graph"></div>
<aside id="panel" hidden>
  <p id="panel-kind"></p>
  <h2 id="panel-title"></h2>
  <pre id="panel-body"></pre>
</aside>
<div id="hint">click a node · drag to pan · scroll to zoom</div>
<script>
const GRAPH = __GRAPH_DATA__;

document.getElementById("title").textContent = GRAPH.meta.title;
document.getElementById("rev").textContent = "rev " + GRAPH.meta.revision;
if (GRAPH.meta.status) {
  const chip = document.getElementById("status");
  chip.hidden = false;
  chip.textContent = GRAPH.meta.status;
  chip.classList.add(GRAPH.meta.status);
}
if (GRAPH.meta.status === "unapproved") {
  const banner = document.getElementById("banner");
  banner.hidden = false;
  banner.textContent = "⚠ shipped unapproved — veto retry budget exhausted"
    + (GRAPH.meta.reasons.length ? ": " + GRAPH.meta.reasons.join(" · ") : "");
}

const nodes = new vis.DataSet(GRAPH.nodes);
const edges = new vis.DataSet(GRAPH.edges);
const network = new vis.Network(
  document.getElementById("graph"),
  { nodes, edges },
  {
    physics: {
      barnesHut: { gravitationalConstant: -4500, springLength: 130,
                   springConstant: 0.02, damping: 0.3 },
      stabilization: { iterations: 250 },
    },
    nodes: { borderWidth: 1.5,
             font: { color: "#c9c9d9", face: "system-ui", size: 13 } },
    edges: { color: { color: "#34344a", highlight: "#8b8bb0" }, width: 1.2,
             smooth: { type: "continuous" } },
    interaction: { hover: true },
  }
);

const panel = document.getElementById("panel");
network.on("click", (params) => {
  if (!params.nodes.length) { panel.hidden = true; return; }
  const node = nodes.get(params.nodes[0]);
  document.getElementById("panel-kind").textContent = node.kind;
  document.getElementById("panel-title").textContent = node.full_label || node.label;
  document.getElementById("panel-body").textContent = node.detail || "(no detail)";
  panel.hidden = false;
});
</script>
</body>
</html>
"""


def render_html(
    compiled: CompiledOutput,
    out_path: Path,
    *,
    results: list[GatherResult] | None = None,
    verdict: VetoVerdict | None = None,
) -> Path:
    """Write the interactive graph page; return its path.

    `results` enriches file nodes with the gatherers' relevance notes and adds
    permission-denied files as locked nodes; `verdict` drives the approved /
    unapproved state on the page. Both optional so the renderer works from a
    bare CompiledOutput.
    """
    graph = _build_graph(compiled, results, verdict)
    # "</" would close the inline <script> if it ever appeared inside embedded
    # file text; escaping it is a no-op for the parsed JSON.
    data = json.dumps(graph).replace("</", "<\\/")

    page = (
        _TEMPLATE.replace("__TITLE__", html.escape(compiled.prompt))
        .replace("__VIS_JS__", VENDOR_JS.read_text(encoding="utf-8"))
        .replace("__GRAPH_DATA__", data)
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
