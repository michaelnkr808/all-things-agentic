"""GCS adapter against the real client library and the real wire protocol.

tests/test_cloud_gcs.py fakes the adapter itself, so it proves the routing but
never proves we speak GCS correctly. This one points the genuine
google-cloud-storage client at a local stub of the two JSON API calls the
adapter makes, which catches the things mocks cannot: prefix handling, how
object keys map to department-relative paths, and the separate /download host
path used for media. No credentials, no network, no Google Cloud account.
"""

import os

import pytest

from agentic.contracts.config import StorageConfig

storage_lib = pytest.importorskip(
    "google.cloud.storage", reason="google-cloud-storage not installed"
)

from tests._fake_gcs import serve  # noqa: E402


@pytest.fixture
def emulated(monkeypatch):
    srv = serve()
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cartographer-test")
    yield StorageConfig(provider="gcs", bucket="cartographer-demo", prefix="finance")
    srv.shutdown()


def _gcs():
    from agentic.gatherers.cloud import gcs
    return gcs


def test_listing_maps_object_keys_to_department_relative_paths(emulated):
    paths = {c.path for c in _gcs().list_candidates(emulated)}
    assert paths == {"q3-actuals.csv", "cloud-renewal.md", "subdir/forecast.md"}


def test_listing_cannot_escape_the_prefix(emulated):
    """The prefix is the containment root: objects outside it must not appear."""
    keys = [c.key for c in _gcs().list_candidates(emulated)]
    assert all(k.startswith("finance/") for k in keys)
    assert not any("other-dept" in k for k in keys)


def test_candidates_carry_their_size(emulated):
    by_path = {c.path: c for c in _gcs().list_candidates(emulated)}
    assert by_path["q3-actuals.csv"].size > 0


def test_download_returns_object_text(emulated):
    body = _gcs().download(emulated, "finance/q3-actuals.csv")
    assert body.startswith("team,line_item,budgeted_usd,actual_usd")


def test_empty_prefix_lists_the_whole_bucket(emulated):
    everything = _gcs().list_candidates(
        StorageConfig(provider="gcs", bucket="cartographer-demo")
    )
    assert any(c.path.startswith("other-dept/") for c in everything)
