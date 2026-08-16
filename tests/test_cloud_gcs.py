"""GCS adapter tests — the google.cloud.storage client is fully mocked, no network."""

import pytest

from agentic.contracts.config import DepartmentConfig, StorageConfig
from agentic.gatherers.cloud import gcs


class _FakeBlob:
    def __init__(self, name: str, size: int, content: bytes):
        self.name = name
        self.size = size
        self._content = content

    def download_as_bytes(self):
        return self._content


class _FakeBucket:
    def __init__(self, blobs: list[_FakeBlob]):
        self._blobs = blobs

    def list_blobs(self, prefix=None):
        return [b for b in self._blobs if b.name.startswith(prefix or "")]

    def blob(self, key: str):
        for b in self._blobs:
            if b.name == key:
                return b
        raise FileNotFoundError(key)


class _FakeClient:
    def __init__(self, blobs: list[_FakeBlob]):
        self._bucket = _FakeBucket(blobs)

    def bucket(self, name: str):
        assert name == "acme-eng"
        return self._bucket


@pytest.fixture(autouse=True)
def _mock_storage_client(monkeypatch):
    blobs = [
        _FakeBlob("docs/roadmap.md", 120, b"# roadmap"),
        _FakeBlob("docs/sprint-notes/q3-retro.txt", 90, b"retro text"),
        _FakeBlob("docs/", 0, b""),  # folder placeholder — must be skipped
        _FakeBlob("other/leak.md", 10, b"secret"),  # outside the prefix — must never appear
    ]
    monkeypatch.setattr(gcs, "_client", lambda: _FakeClient(blobs))


def _storage(**kw) -> StorageConfig:
    base = {"provider": "gcs", "bucket": "acme-eng", "prefix": "docs/"}
    base.update(kw)
    return StorageConfig(**base)


def test_list_candidates_strips_prefix_and_skips_placeholders():
    cands = gcs.list_candidates(_storage())
    paths = {c.path for c in cands}
    assert paths == {"roadmap.md", "sprint-notes/q3-retro.txt"}
    assert all(c.key.startswith("docs/") for c in cands)
    assert all(c.size is not None for c in cands)


def test_download_returns_text():
    content = gcs.download(_storage(), "docs/roadmap.md")
    assert content == "# roadmap"


async def test_gatherer_cloud_path_via_dispatch():
    from agentic.contracts.config import load_config
    from agentic.contracts.messages import GatherRequest
    from agentic.gatherers.gatherer import gather_files

    config = load_config("config/environment.example.yaml")
    config.departments[0].storage = _storage()
    config.departments[0].allowed_roles = ["analyst", "admin"]
    request = GatherRequest(request_id="r1", department="engineering", query="roadmap")
    result = await gather_files(request, config)
    assert {f.path for f in result.files} == {
        "roadmap.md",
        "sprint-notes/q3-retro.txt",
    }
    assert "other/leak.md" not in {f.path for f in result.files}
