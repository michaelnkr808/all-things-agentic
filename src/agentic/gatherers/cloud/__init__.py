"""Cloud backends (gcs / drive) — the only code that talks to GCP.

Departments with a ``storage:`` block in environment.yaml read their files
from here instead of the local filesystem. Adapters expose a Candidate
listing plus a download; relevance ranking, the permission pre-check, and
file caps stay in gatherer.py, shared with the local gatherer.

Credentials come from Application Default Credentials (service account via
GOOGLE_APPLICATION_CREDENTIALS). The service account should be scoped with
least privilege: each department's bucket/prefix or Drive folder is all it
can reach, so a cloud department's IAM scope and the config's permission
scope agree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """One readable cloud object, as exposed to the gatherer."""

    key: str  # backend identifier (object key / drive file id)
    path: str  # department-relative path (permissions + [[wikilinks]])
    size: int | None  # bytes when the backend knows; None for exports
