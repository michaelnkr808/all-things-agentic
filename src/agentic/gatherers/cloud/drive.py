"""Google Drive adapter (Malik).

Lists everything under a department's root Drive folder (``folder_id``),
derives department-relative paths from the folder hierarchy, and downloads
files. Native Google files (Docs/Sheets/Slides) are exported to text;
shortcuts are resolved to their target before gating. A file is only
surfaced when its parent chain reaches the department's root folder —
anything else (a shortcut pointing outside, a stray multi-parent file) is
silently excluded by the adapter and denied at the spawn gate.
"""

from __future__ import annotations

from agentic.contracts.config import StorageConfig

from agentic.gatherers.cloud import Candidate

_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.slides": "text/plain",
}

_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


def _service():
    from google.auth import default
    from googleapiclient.discovery import build

    creds, _ = default()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_page(service, drive_id, parent_id, page_token):
    return (
        service.files()
        .list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, parents, size, shortcutDetails)",
            pageToken=page_token,
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            driveId=drive_id,
        )
        .execute()
    )


def _resolve_shortcut(service, node):
    """Replace a shortcut with its target so name/mime/parents are the real file's."""
    target_id = node.get("shortcutDetails", {}).get("targetId")
    if not target_id:
        return None
    target = (
        service.files()
        .get(fileId=target_id, fields="id, name, mimeType, parents, size", supportsAllDrives=True)
        .execute()
    )
    return target


def _walk_tree(service, drive_id, folder_id) -> dict[str, dict]:
    """Collect every file under ``folder_id`` (nested), keyed by file id."""
    nodes: dict[str, dict] = {}
    visited: set[str] = set()
    stack = [folder_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        page_token = None
        while True:
            page = _list_page(service, drive_id, current, page_token)
            for f in page.get("files", []):
                if f.get("mimeType") == _SHORTCUT_MIME:
                    f = _resolve_shortcut(service, f) or f
                if f.get("mimeType") == _FOLDER_MIME:
                    stack.append(f["id"])
                nodes[f["id"]] = f
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    return nodes


def _parent_chain(nodes: dict[str, dict], file_id: str, root_id: str, memo: dict) -> list[str] | None:
    """Ancestor id chain root_id -> ... -> file_id, or None if not under root."""
    if file_id == root_id:
        return [root_id]
    if file_id in memo:
        return memo[file_id]
    node = nodes.get(file_id)
    memo[file_id] = None  # cycle guard
    if node is None:
        return None
    for pid in node.get("parents", []):
        chain = _parent_chain(nodes, pid, root_id, memo)
        if chain is not None:
            memo[file_id] = chain + [file_id]
            return memo[file_id]
    return None


def _relative_path(nodes: dict[str, dict], file_id: str, root_id: str) -> str | None:
    chain = _parent_chain(nodes, file_id, root_id, {})
    if chain is None or len(chain) < 2:
        return None
    parts = [nodes[cid]["name"] for cid in chain[1:]]  # root folder excluded
    return "/".join(parts)


def list_candidates(storage: StorageConfig) -> list[Candidate]:
    """All files under the department root folder, as department-relative paths."""
    service = _service()
    nodes = _walk_tree(service, storage.folder_id, storage.folder_id)
    candidates: list[Candidate] = []
    for fid, node in nodes.items():
        if node.get("mimeType") == _FOLDER_MIME:
            continue
        rel = _relative_path(nodes, fid, storage.folder_id)
        if rel is None:
            continue  # not under the department root -> excluded
        size = node.get("size")
        candidates.append(
            Candidate(key=fid, path=rel, size=int(size) if size else None)
        )
    return candidates


def download(storage: StorageConfig, key: str) -> str:
    """Fetch a file's content as text, exporting native Google files."""
    service = _service()
    meta = (
        service.files()
        .get(fileId=key, fields="mimeType", supportsAllDrives=True)
        .execute()
    )
    export_mime = _EXPORT_MIME.get(meta.get("mimeType", ""))
    if export_mime:
        body = service.files().export(fileId=key, mimeType=export_mime).execute()
    else:
        body = service.files().get_media(fileId=key, supportsAllDrives=True).execute()
    return body.decode("utf-8", errors="replace")
