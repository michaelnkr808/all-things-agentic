"""Render the Cartographer architecture sheet to a single-page PDF.

    pip install reportlab        # not a project dependency; this script only
    python scripts/render_architecture.py


Mirrors docs/architecture.html; that page is the source of truth for the
wording, this is the uploadable artifact Devpost wants.
"""
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit

W, H = 1400, 900
OUT = "/home/michaelnkr808/Desktop/all-things-agentic/docs/architecture.pdf"

GROUND = HexColor("#0f0f16")
PANEL  = HexColor("#1a1a25")
BORDER = HexColor("#2f2f42")
TEXT   = HexColor("#e9e9f2")
MUTED  = HexColor("#969cb3")
TEAL   = HexColor("#5eead4")
AMBER  = HexColor("#fbbf24")
VIOLET = HexColor("#a78bfa")
ROSE   = HexColor("#fb7185")
BLUE   = HexColor("#60a5fa")

B, R, M = "Helvetica-Bold", "Helvetica", "Helvetica-Oblique"
c = canvas.Canvas(OUT, pagesize=(W, H))
c.setTitle("Cartographer - Architecture")
c.setAuthor("michaelnkr808")

c.setFillColor(GROUND); c.rect(0, 0, W, H, fill=1, stroke=0)

def box(x, y, w, h, fill=PANEL, stroke=BORDER, r=8, lw=1):
    c.setFillColor(fill); c.setStrokeColor(stroke); c.setLineWidth(lw)
    c.roundRect(x, y, w, h, r, fill=1, stroke=1)

def txt(x, y, s, font=R, size=9, color=TEXT):
    c.setFillColor(color); c.setFont(font, size); c.drawString(x, y, s)

def ctr(x, y, s, font=R, size=9, color=TEXT):
    c.setFillColor(color); c.setFont(font, size); c.drawCentredString(x, y, s)

def wrap(x, y, s, w, font=R, size=8, color=MUTED, lead=10.5):
    c.setFillColor(color); c.setFont(font, size)
    for i, line in enumerate(simpleSplit(s, font, size, w)):
        c.drawString(x, y - i * lead, line)
    return y - len(simpleSplit(s, font, size, w)) * lead


def hexagon(cx, cy, r):
    """Flat-top hexagon, drawn rather than typed: U+2B22 is not in WinAnsi."""
    import math
    p = c.beginPath()
    for i in range(6):
        a = math.radians(60 * i)
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    p.close()
    c.setFillColor(TEAL); c.setStrokeColor(TEAL); c.setLineWidth(1)
    c.drawPath(p, fill=1, stroke=1)

def tri(cx, cy, s=4.5):
    p = c.beginPath()
    p.moveTo(cx - s, cy + s); p.lineTo(cx + s, cy + s); p.lineTo(cx, cy - s); p.close()
    c.setFillColor(AMBER); c.drawPath(p, fill=1, stroke=0)

# ── header ────────────────────────────────────────────────────────────────
hexagon(62, H - 50, 11)
txt(82, H - 58, "Cartographer", B, 26, TEXT)
txt(50, H - 80, "Permission-gated enterprise agent fleet  ·  one prompt, six stations, five gates",
    R, 11, MUTED)
bw = 210
box(W - 50 - bw, H - 76, bw, 26, fill=HexColor("#1d2b28"), stroke=TEAL, r=13)
ctr(W - 50 - bw / 2, H - 68, "Fortified Enterprise Fleet", B, 9.5, TEAL)

# ── input bar ─────────────────────────────────────────────────────────────
y = H - 132
box(50, y, W - 100, 44, fill=HexColor("#16161f"))
txt(66, y + 27, "INPUT", B, 7.5, TEAL)
txt(66, y + 12, "“How did Q3 engineering spend compare to the finance budget?”", M, 11, TEXT)
txt(W - 66 - c.stringWidth("role from verified JWT · never from the request body", R, 8.5),
    y + 12, "role from verified JWT · never from the request body", R, 8.5, MUTED)

# ── five stations ─────────────────────────────────────────────────────────
STATIONS = [
    ("01 · PLAN", "State manager", VIOLET,
     "Splits the prompt into one gathering task per department. The output schema is a closed set "
     "built per request, so an invented department fails validation before any code runs.",
     "gemini-3.5-flash · Vertex AI · Google ADK"),
    ("02 · GATHER", "Gatherer fleet", BLUE,
     "One agent per department, fanned out concurrently under a configurable cap. Globs, gates, "
     "reads, then assesses each file for relevance.",
     "gemini-3.5-flash · parallel"),
    ("03 · SYNTHESISE", "Synthesiser", TEAL,
     "Compiles one answer in Obsidian-flavoured markdown with inline [[dept/path]] citations back "
     "to the files it actually used.",
     "gemini-3.5-flash"),
    ("04 · REJECT", "Veto checker", ROSE,
     "Adversarial. Receives the full source text, not just the citations, and tries to reject "
     "unsupported claims and citations that do not resolve. Vetoes carry revision notes.",
     "Claude · structured verdict · operator allowlist"),
    ("05 · DRAW", "Map", AMBER,
     "Departments, cited files, and every denied file drawn dashed and locked. One graph builder "
     "feeds the live canvas and a self-contained offline page.",
     "canvas · streamed over SSE"),
]
sy, sh = H - 320, 168
gap, n = 14, len(STATIONS)
sw = (W - 100 - gap * (n - 1)) / n
for i, (tag, name, col, desc, foot) in enumerate(STATIONS):
    x = 50 + i * (sw + gap)
    box(x, sy, sw, sh)
    c.setFillColor(col); c.rect(x, sy + sh - 4, sw, 4, fill=1, stroke=0)
    txt(x + 14, sy + sh - 22, tag, B, 7.5, col)
    txt(x + 14, sy + sh - 42, name, B, 13.5, TEXT)
    wrap(x + 14, sy + sh - 62, desc, sw - 28)
    c.setFillColor(BORDER); c.rect(x + 14, sy + 30, sw - 28, 0.7, fill=1, stroke=0)
    wrap(x + 14, sy + 18, foot, sw - 28, font=B, size=7.2, color=col, lead=9)
    if i < n - 1:
        c.setStrokeColor(BORDER); c.setLineWidth(1.2)
        c.line(x + sw + 3, sy + sh / 2, x + sw + gap - 3, sy + sh / 2)
        c.setFillColor(BORDER)
        c.circle(x + sw + gap - 3, sy + sh / 2, 2.2, fill=1, stroke=0)

lab = "E V E R Y   F I L E ,   B E F O R E   I T   I S   O P E N E D"
ctr(W / 2, sy - 20, lab, B, 8, AMBER)
half = c.stringWidth(lab, B, 8) / 2
tri(W / 2 - half - 16, sy - 17)
tri(W / 2 + half + 16, sy - 17)

# ── the gate ──────────────────────────────────────────────────────────────
gy, gh = sy - 196, 158
box(50, gy, W - 100, gh, fill=HexColor("#191410"), stroke=AMBER, lw=1.4)
txt(68, gy + gh - 26, "THE GATE", B, 14, AMBER)
txt(150, gy + gh - 26, "non-bypassable  ·  fails closed  ·  in front of the read, not behind the answer",
    R, 9, MUTED)

GATES = [
    ("01", "Path check", "Decided on the path. A file you may not read is never opened, so its contents cannot reach a prompt."),
    ("02", "Two configs agree", "permissions.yaml AND environment.yaml must both grant it. Disagreement denies."),
    ("03", "Spawn re-check", "Every file a gatherer returns is re-checked centrally against the trusted role."),
    ("04", "Containment", "Traversal, absolute paths and escaping symlinks rejected. Cloud keys checked against the bucket prefix."),
    ("05", "Provenance", "The path must be a file the department actually holds, so an invented path cannot wear a real department's name."),
]
ggap = 12
gw = (W - 136 - ggap * 4) / 5
for i, (num, name, desc) in enumerate(GATES):
    x = 68 + i * (gw + ggap)
    box(x, gy + 44, gw, 76, fill=HexColor("#221b12"), stroke=HexColor("#4a3a1e"), r=6)
    txt(x + 11, gy + 104, num, B, 7.5, AMBER)
    txt(x + 30, gy + 104, name, B, 10, TEXT)
    wrap(x + 11, gy + 90, desc, gw - 22, size=7.3, lead=9)

c.setFillColor(HexColor("#0d0b07")); c.setStrokeColor(HexColor("#4a3a1e")); c.setLineWidth(1)
c.roundRect(68, gy + 12, W - 136, 24, 5, fill=1, stroke=1)
ctr(W / 2, gy + 20,
    "read(role, dept, path)   <=>   G_perm(role,dept)  AND  G_env(role,dept)  AND  contained(path,dept)  AND  exists(path,dept)",
    "Courier-Bold", 10, AMBER)

# ── bottom three panels ───────────────────────────────────────────────────
by, bh = 56, gy - 56 - 16
pw = (W - 100 - 28) / 3

box(50, by, pw, bh)
txt(64, by + bh - 20, "SOURCES", B, 7.5, BLUE)
txt(64, by + bh - 38, "One gate, any backend", B, 12, TEXT)
yy = wrap(64, by + bh - 56,
          "A cloud object passes exactly the same gate as a local file. For GCS the department name "
          "is the bucket prefix and the prefix is the containment root.", pw - 28)
for label, col in (("local filesystem", MUTED), ("Google Cloud Storage", BLUE), ("Google Drive", BLUE)):
    yy -= 15
    c.setFillColor(col); c.circle(70, yy + 3, 2.6, fill=1, stroke=0)
    txt(80, yy, label, B, 8.5, TEXT)
yy -= 15
c.setFillColor(ROSE); c.circle(70, yy + 3, 2.6, fill=0, stroke=1); c.setStrokeColor(ROSE)
c.circle(70, yy + 3, 2.6, fill=0, stroke=1)
txt(80, yy, "denied · drawn, not dropped", B, 8.5, ROSE)

x2 = 50 + pw + 14
box(x2, by, pw, bh)
txt(x2 + 14, by + bh - 20, "EVIDENCE", B, 7.5, TEAL)
txt(x2 + 14, by + bh - 38, "Proving it, not just doing it", B, 12, TEXT)
yy = by + bh - 58
for head, tail in (
    ("Access ledger", " · append-only record of every decision at both gates, pinned to the caller"),
    ("Citation coverage", " · integrity, grounding and usage, computed with no model call"),
    ("Role comparison", " · one prompt under two roles, downward only"),
):
    c.setFillColor(TEAL); c.setFont(B, 8.5); c.drawString(x2 + 14, yy, head)
    off = c.stringWidth(head, B, 8.5)
    yy = wrap(x2 + 14 + off, yy, tail, pw - 28 - off, size=8.5, lead=10.5) - 8

x3 = 50 + 2 * (pw + 14)
box(x3, by, pw, bh)
txt(x3 + 14, by + bh - 20, "SURFACE", B, 7.5, VIOLET)
txt(x3 + 14, by + bh - 38, "Backend and client", B, 12, TEXT)
yy = by + bh - 58
for head, tail in (
    ("FastAPI + SSE", " · the run streams file by file; cancel is authorised against the JWT"),
    ("JWT auth", " · pbkdf2-sha256 credentials, per-IP login rate limiting"),
    ("Replay", " · a recorded run plays back with zero API calls, pinned to the recording's role"),
):
    c.setFillColor(VIOLET); c.setFont(B, 8.5); c.drawString(x3 + 14, yy, head)
    off = c.stringWidth(head, B, 8.5)
    yy = wrap(x3 + 14 + off, yy, tail, pw - 28 - off, size=8.5, lead=10.5) - 8

# ── footer ────────────────────────────────────────────────────────────────
txt(50, 34, "245 tests · 4,061 lines of test to 4,852 of source", B, 8.5, MUTED)
txt(50, 20, "Google ADK · google-genai · google-cloud-storage · Vertex AI · Cloud Run · FastAPI · Pydantic", R, 8, MUTED)
s = "github.com/michaelnkr808/cartographer"
txt(W - 50 - c.stringWidth(s, B, 8.5), 20, s, B, 8.5, TEAL)

c.showPage(); c.save()
print("wrote", OUT)
