"""Shared message schemas — the seam between Michael's and Malik's code.

Changing anything here requires agreement from both of us.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GatherRequest(BaseModel):
    """State manager (Malik: spawn.py) -> a single gatherer (Michael: gather.py)."""

    request_id: str
    department: str
    query: str  # natural-language description of what to look for
    keywords: list[str] = Field(default_factory=list)  # loose match hints
    max_files: int = 10
    requester_role: str = "analyst"  # checked by permissions.py (Malik)


class GatheredFile(BaseModel):
    path: str  # relative to the department root, used for [[wikilinks]]
    department: str
    content: str
    relevance_note: str = ""  # one line: why the gatherer grabbed it


class GatherResult(BaseModel):
    """Gatherer -> state manager."""

    request_id: str
    department: str
    files: list[GatheredFile] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)  # paths blocked by permissions
    errors: list[str] = Field(default_factory=list)


class CompiledOutput(BaseModel):
    """State manager -> veto checker -> client.

    `obsidian_markdown` is the single rendered artifact: frontmatter +
    answer body + [[wikilinks]] to every source. Obsidian-flavored syntax
    only — Michael's output.py produces it; Michael's viz.py renders it
    in our own visualizer (not the Obsidian app).
    """

    prompt: str  # original user prompt, verbatim
    obsidian_markdown: str
    sources: list[str] = Field(default_factory=list)  # dept/path strings
    departments_used: list[str] = Field(default_factory=list)
    revision: int = 0  # bumped each time the veto checker sends it back


class VetoVerdict(BaseModel):
    """Veto checker -> state manager (on veto) or client (on approve)."""

    approved: bool
    reasons: list[str] = Field(default_factory=list)
    revision_notes: str = ""  # concrete instructions for the revise pass
