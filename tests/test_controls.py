"""Frontend control surface (Michael): /api/fleet, per-run overrides, cancel.

The pipeline is faked at the same seams test_server.py uses, so these pin the
HTTP contract the browser builds against — not model behaviour. No API keys.
"""

import pytest

from agentic.contracts.config import load_config
from agentic.server import runs
from agentic.server.runs import OverrideError, apply_overrides, build_fleet

from tests.test_server import client, _login  # noqa: F401  (fixture reuse)

CONFIG = "config/environment.example.yaml"


@pytest.fixture
def config():
    return load_config(CONFIG)


@pytest.fixture(autouse=True)
def _example_permissions(monkeypatch):
    """Never depend on an untracked local permissions.yaml."""
    monkeypatch.setattr(
        "agentic.gatherers.permissions.DEFAULT_PERMISSIONS_CONFIG",
        "config/permissions.example.yaml",
    )


# ---------------- fleet ----------------

def test_fleet_locks_hr_for_analyst_and_opens_it_for_admin(config):
    analyst = {d.name: d.readable for d in build_fleet("analyst", config).departments}
    admin = {d.name: d.readable for d in build_fleet("admin", config).departments}

    assert analyst == {"engineering": True, "finance": True, "hr": False}
    assert admin == {"engineering": True, "finance": True, "hr": True}


def test_fleet_reports_cloud_backends(config, monkeypatch):
    """The ☁ badge must come from real config, not a hardcoded frontend list."""
    from agentic.contracts.config import StorageConfig

    config.department("finance").storage = StorageConfig(
        provider="gcs", bucket="acme-finance"
    )
    fleet = build_fleet("analyst", config)
    by_name = {d.name: d.storage for d in fleet.departments}
    assert by_name["finance"] == "gcs"
    assert by_name["engineering"] is None


def test_fleet_endpoint_requires_auth(client):
    assert client.get("/api/fleet").status_code == 401


def test_fleet_endpoint_reflects_the_token_role(client):
    for user, password, hr_readable in [
        ("alice", "wonderland", False),
        ("root", "toor", True),
    ]:
        token = _login(client, user, password)["access_token"]
        res = client.get("/api/fleet", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, res.text
        body = res.json()
        hr = next(d for d in body["departments"] if d["name"] == "hr")
        assert hr["readable"] is hr_readable


# ---------------- overrides ----------------

def test_overrides_clamp_to_the_operator_ceiling(config):
    config.gatherers.max_gatherers = 4
    config.veto.max_retries = 2

    tightened = apply_overrides(config, max_gatherers=2, max_retries=0)
    assert tightened.gatherers.max_gatherers == 2
    assert tightened.veto.max_retries == 0

    # A client asking for more than the operator allows gets the ceiling.
    greedy = apply_overrides(config, max_gatherers=999, max_retries=999)
    assert greedy.gatherers.max_gatherers == 4
    assert greedy.veto.max_retries == 2


def test_overrides_never_mutate_the_loaded_config(config):
    before = config.gatherers.max_gatherers
    apply_overrides(config, max_gatherers=1)
    assert config.gatherers.max_gatherers == before, "concurrent runs would collide"


def test_veto_model_must_be_allowlisted(config):
    config.models.veto_choices = ["claude-fable-5", "claude-opus-5"]

    assert apply_overrides(config, veto_model="claude-opus-5").models.veto == "claude-opus-5"

    with pytest.raises(OverrideError):
        apply_overrides(config, veto_model="some-other-model")
    with pytest.raises(OverrideError):
        # Free text reaching a model id is exactly what the allowlist prevents.
        apply_overrides(config, veto_model="../../etc/passwd")


def test_department_scoping_only_narrows(config):
    scoped = apply_overrides(config, departments=["engineering"])
    assert [d.name for d in scoped.departments] == ["engineering"]


def test_unknown_department_is_rejected_not_ignored(config):
    """A typo must fail loudly rather than quietly shrinking the answer."""
    with pytest.raises(OverrideError):
        apply_overrides(config, departments=["engineering", "finence"])


def test_empty_department_selection_is_rejected(config):
    with pytest.raises(OverrideError):
        apply_overrides(config, departments=[])


def test_bad_override_is_a_4xx_not_a_broken_stream(client):
    client.install_fakes()
    token = _login(client)["access_token"]
    res = client.post(
        "/api/run",
        json={"prompt": "q", "departments": ["nope"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "nope" in res.json()["detail"]


# ---------------- cancel ----------------

def test_cancelling_an_unknown_run_is_404(client):
    token = _login(client)["access_token"]
    res = client.post(
        "/api/run/deadbeef/cancel", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 404


def test_cancel_requires_auth(client):
    assert client.post("/api/run/deadbeef/cancel").status_code == 401


def test_one_user_cannot_cancel_another_users_run(client, monkeypatch):
    """Knowing a run_id must not be enough to kill someone else's run."""
    import asyncio

    from agentic.server import app as app_module

    async def forever():
        await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    task = loop.create_task(forever())
    app_module._active_runs["run-owned-by-root"] = (task, "root")
    try:
        token = _login(client, "alice", "wonderland")["access_token"]
        res = client.post(
            "/api/run/run-owned-by-root/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Indistinguishable from "no such run": whether root has a run in
        # flight is not alice's business.
        assert res.status_code == 404
        assert not task.cancelled()
    finally:
        app_module._active_runs.pop("run-owned-by-root", None)
        task.cancel()
        loop.close()
