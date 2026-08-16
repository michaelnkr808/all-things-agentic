"""Drive adapter tests — the Drive API client is fully mocked, no network."""

import pytest

from agentic.contracts.config import StorageConfig
from agentic.gatherers.cloud import drive


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    def execute(self):
        return self._body


class _FakeFiles:
    def __init__(self, files):
        self._files = {f["id"]: f for f in files}

    def list(self, **params):
        parent = params["q"].split("'")[1]
        page = [f for f in self._files.values() if parent in f.get("parents", [])]
        return _FakeRequest({"files": page})

    def get(self, **params):
        return _FakeRequest(self._files[params["fileId"]])

    def export(self, **params):
        fid = params["fileId"]
        return _FakeRequest(self._files[fid]["_export"].encode("utf-8"))

    def get_media(self, **params):
        fid = params["fileId"]
        return _FakeRequest(self._files[fid]["_content"])


class _FakeService:
    def __init__(self, files):
        self._files = _FakeFiles(files)

    def files(self):
        return self._files


def _files():
    return [
        {"id": "roadmap", "name": "roadmap.md", "mimeType": "text/plain", "parents": ["root"], "size": "120", "_content": b"# roadmap"},
        {"id": "sub", "name": "sprint-notes", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]},
        {"id": "retro", "name": "q3-retro.txt", "mimeType": "text/plain", "parents": ["sub"], "size": "90", "_content": b"retro"},
        {"id": "budget", "name": "q3-budget", "mimeType": "application/vnd.google-apps.spreadsheet", "parents": ["root"], "_export": "category,amount\ncloud,420\n"},
        {"id": "sc-inside", "name": "inside-link", "mimeType": "application/vnd.google-apps.shortcut", "parents": ["root"], "shortcutDetails": {"targetId": "target-inside"}},
        {"id": "target-inside", "name": "inside.md", "mimeType": "text/plain", "parents": ["root"], "size": "10", "_content": b"inside"},
        {"id": "sc-outside", "name": "outside-link", "mimeType": "application/vnd.google-apps.shortcut", "parents": ["root"], "shortcutDetails": {"targetId": "target-outside"}},
        {"id": "target-outside", "name": "leak.md", "mimeType": "text/plain", "parents": ["other-folder"], "size": "10", "_content": b"secret"},
    ]


@pytest.fixture(autouse=True)
def _mock_service(monkeypatch):
    monkeypatch.setattr(drive, "_service", lambda: _FakeService(_files()))


def _storage(**kw) -> StorageConfig:
    base = {"provider": "drive", "folder_id": "root"}
    base.update(kw)
    return StorageConfig(**base)


def test_list_candidates_builds_relative_paths():
    cands = drive.list_candidates(_storage())
    paths = {c.path for c in cands}
    assert paths == {
        "roadmap.md",
        "sprint-notes/q3-retro.txt",
        "q3-budget",
        "inside.md",
    }
    assert all(c.key for c in cands)


def test_shortcut_pointing_outside_root_is_excluded():
    cands = drive.list_candidates(_storage())
    assert "leak.md" not in {c.path for c in cands}


def test_download_regular_file():
    assert drive.download(_storage(), "roadmap") == "# roadmap"


def test_download_exports_native_spreadsheet():
    assert drive.download(_storage(), "budget") == "category,amount\ncloud,420\n"
