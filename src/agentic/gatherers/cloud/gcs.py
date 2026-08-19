"""GCS adapter (Malik).

Lists every object under the department's ``prefix`` and downloads object
content as text. Object keys are mapped to department-relative paths by
stripping the prefix; the prefix is the logical containment root, so only
objects under it can ever appear. No file here is returned without first
passing the same permission checks as local files (gatherer.py + the
mandatory spawn gate).
"""

from __future__ import annotations

from agentic.contracts.config import StorageConfig

from agentic.gatherers.cloud import Candidate


def _client():
    from google.cloud import storage

    return storage.Client()


def _prefix(storage: StorageConfig) -> str:
    return storage.prefix.rstrip("/") + "/" if storage.prefix else ""


def list_candidates(storage: StorageConfig) -> list[Candidate]:
    """All objects under the department's prefix, as department-relative paths."""
    bucket = _client().bucket(storage.bucket)
    prefix = _prefix(storage)
    candidates: list[Candidate] = []
    for blob in bucket.list_blobs(prefix=prefix):
        rel = blob.name[len(prefix) :] if prefix else blob.name
        rel = rel.strip("/")
        if not rel:
            continue  # folder placeholders
        candidates.append(Candidate(key=blob.name, path=rel, size=blob.size))
    return candidates


def download(storage: StorageConfig, key: str) -> str:
    """Fetch an object's content as text."""
    blob = _client().bucket(storage.bucket).blob(key)
    return blob.download_as_bytes().decode("utf-8", errors="replace")
