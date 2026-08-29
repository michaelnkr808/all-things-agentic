"""SSE backend tests (provisional — Malik).

The pipeline is faked at the manager/checker/viz seams (same approach as
test_pipeline.py) so these pin the HTTP surface: auth gating, the SSE
event sequence, and that the JWT role actually reaches plan_and_gather.
No API keys.
"""

import pytest
from fastapi.testclient import TestClient

from agentic.contracts.messages import CompiledOutput, GatheredFile, GatherResult, VetoVerdict
from agentic.server import auth
from agentic.server.app import create_app
from agentic.server.sse import parse_stream

SECRET = "test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """App wired to temp configs and faked pipeline internals."""
    monkeypatch.setenv("AGENTIC_JWT_SECRET", SECRET)
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n"
        "  alice:\n"
        "    role: analyst\n"
        f"    password_hash: {auth.hash_password('wonderland')}\n"
        "  root:\n"
        "    role: admin\n"
        f"    password_hash: {auth.hash_password('toor')}\n"
    )
    monkeypatch.setenv("AGENTIC_USERS_PATH", str(users))
    monkeypatch.setenv("AGENTIC_CONFIG_PATH", "config/environment.example.yaml")
    # Tests must not depend on an untracked local permissions.yaml.
    monkeypatch.setattr(
        "agentic.gatherers.permissions.DEFAULT_PERMISSIONS_CONFIG",
        "config/permissions.example.yaml",
    )

    def install_fakes(verdicts=(True,)):
        seen = {"requester_role": None}

        async def fake_plan_and_gather(prompt, config, requester_role=None, on_result=None, on_progress=None):
            seen["requester_role"] = requester_role
            eng = GatherResult(request_id="r0", department="engineering")
            if requester_role == "admin":
                eng.files = [GatheredFile(path="roadmap.md", department="engineering", content="c")]
            if on_result:
                on_result(None, eng)
            results = [eng]
            hr = GatherResult(request_id="r1", department="hr")
            if requester_role == "admin":
                hr.files = [
                    GatheredFile(path="comp-bands.md", department="hr", content="c")
                ]
                if on_result:
                    on_result(None, hr)
            else:
                hr.denied = ["comp-bands.md"]
                if on_result:
                    on_result(None, hr)
            results.append(hr)
            return results

        async def fake_synthesize(prompt, results, config):
            return CompiledOutput(prompt=prompt, obsidian_markdown="answer", sources=[])

        async def fake_revise(prompt, previous, verdict, results, config):
            return previous.model_copy(update={"revision": previous.revision + 1})

        checks = {"n": 0}

        async def fake_check(prompt, compiled, config, results):
            approved = verdicts[min(checks["n"], len(verdicts) - 1)]
            checks["n"] += 1
            return VetoVerdict(approved=approved, reasons=["fake"])

        def fake_render(compiled, out_path, **kwargs):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("<html></html>")
            return out_path

        from agentic.state_manager import manager
        from agentic.veto import checker
        from agentic.client import viz

        monkeypatch.setattr(manager, "plan_and_gather", fake_plan_and_gather)
        monkeypatch.setattr(manager, "synthesize", fake_synthesize)
        monkeypatch.setattr(manager, "revise", fake_revise)
        monkeypatch.setattr(checker, "check", fake_check)
        monkeypatch.setattr(viz, "render_html", fake_render)
        return seen

    client = TestClient(create_app())
    client.install_fakes = install_fakes
    yield client


def _login(client, username="alice", password="wonderland"):
    res = client.post(
        "/api/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return res.json()


def _run_events(client, token, prompt="q"):
    res = client.post(
        "/api/run",
        json={"prompt": prompt},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    return parse_stream(res.text)


# ---- health / static ----

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Cartographer" in res.text


# ---- login ----

def test_login_returns_token_and_role(client):
    data = _login(client, "root", "toor")
    assert data["token_type"] == "bearer"
    assert data["role"] == "admin"
    assert auth.verify_token(data["access_token"], SECRET).username == "root"


def test_login_bad_password_is_401(client):
    res = client.post("/api/login", json={"username": "alice", "password": "nope"})
    assert res.status_code == 401


def test_login_unknown_user_is_also_401(client):
    res = client.post("/api/login", json={"username": "mallory", "password": "x"})
    assert res.status_code == 401


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(5):
        res = client.post("/api/login", json={"username": "alice", "password": "nope"})
        assert res.status_code == 401
    res = client.post("/api/login", json={"username": "alice", "password": "nope"})
    assert res.status_code == 429
    # even correct credentials are refused while the window is hot
    assert client.post(
        "/api/login", json={"username": "alice", "password": "wonderland"}
    ).status_code == 429


# ---- demo mode ----

def test_demo_mode_disabled_by_default(client, monkeypatch):
    # Hermetic: conftest loads the repo .env, and a developer running local
    # demos has AGENTIC_DEMO_MODE=1 there. The default under test is the
    # deployed default, not whatever happens to be on this machine.
    monkeypatch.delenv("AGENTIC_DEMO_MODE", raising=False)
    data = client.get("/api/demo-mode").json()
    assert data == {"enabled": False, "identities": []}


def test_demo_mode_serves_credentials_only_when_enabled(client, monkeypatch, tmp_path):
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n"
        "  alice:\n"
        "    role: analyst\n"
        f"    password_hash: {auth.hash_password('wonderland')}\n"
        "    demo_password: wonderland\n"  # must match the hashed password
        "  root:\n"
        "    role: admin\n"
        f"    password_hash: {auth.hash_password('toor')}\n"  # no demo_password
    )
    monkeypatch.setenv("AGENTIC_USERS_PATH", str(users))
    monkeypatch.setenv("AGENTIC_DEMO_MODE", "1")

    data = client.get("/api/demo-mode").json()
    assert data["enabled"] is True
    # only identities with a demo_password are served; the admin is not
    assert data["identities"] == [
        {"username": "alice", "role": "analyst", "password": "wonderland"}
    ]

    # and the credentials actually work at /api/login
    login = client.post(
        "/api/login", json={"username": "alice", "password": "wonderland"}
    )
    assert login.status_code == 200


def test_demo_mode_without_flag_never_leaks_credentials(client, monkeypatch, tmp_path):
    """Even with demo_passwords present, no flag means nothing is served."""
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n"
        "  alice:\n"
        "    role: analyst\n"
        f"    password_hash: {auth.hash_password('wonderland')}\n"
        "    demo_password: wonderland\n"
    )
    monkeypatch.setenv("AGENTIC_USERS_PATH", str(users))
    monkeypatch.delenv("AGENTIC_DEMO_MODE", raising=False)

    data = client.get("/api/demo-mode").json()
    assert "wonderland" not in str(data)


# ---- auth gate on /api/run ----

def test_run_without_token_is_401(client):
    assert client.post("/api/run", json={"prompt": "q"}).status_code == 401


def test_run_with_garbage_token_is_401(client):
    res = client.post(
        "/api/run",
        json={"prompt": "q"},
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert res.status_code == 401


def test_run_with_missing_secret_is_503(client, monkeypatch):
    monkeypatch.delenv("AGENTIC_JWT_SECRET", raising=False)
    res = client.post(
        "/api/run",
        json={"prompt": "q"},
        headers={"Authorization": "Bearer whatever"},  # signature check needs the secret
    )
    assert res.status_code in (401, 503)


# ---- SSE run ----

def test_admin_run_streams_full_sequence_and_threads_role(client):
    seen = client.install_fakes()
    events = _run_events(client, _login(client, "root", "toor")["access_token"])
    names = [name for name, _ in events]

    assert names[0] == "run_started"
    assert names.count("gatherer_result") >= 2
    assert names.count("gathered") == 1
    assert names.count("synthesized") == 1
    assert names.count("veto") == 1
    assert names[-1] == "run_state"

    by_name = dict(events)
    assert by_name["gathered"]["kept"] >= 1
    state = by_name["run_state"]
    assert state["approved"] is True
    assert state["attempts"] == 1
    assert state["obsidian_markdown"] == "answer"
    assert state["viz_url"].startswith("/graphs/")
    # the authenticated JWT role reached the permission layer
    assert seen["requester_role"] == "admin"


def test_analyst_run_denies_hr_via_gate(client):
    seen = client.install_fakes()
    events = _run_events(client, _login(client)["access_token"])

    hr_events = [p for n, p in events if n == "gatherer_result" and p["department"] == "hr"]
    assert hr_events and hr_events[0]["kept"] == 0
    assert "comp-bands.md" in hr_events[0]["denied"]
    assert dict(events)["run_state"]["viz_url"].startswith("/graphs/")
    assert seen["requester_role"] == "analyst"


def test_veto_retry_streams_revising_then_approves(client):
    client.install_fakes(verdicts=(False, False, True))
    events = _run_events(client, _login(client)["access_token"])
    names = [name for name, _ in events]

    assert names.count("veto") == 3
    assert names.count("revising") == 2
    # initial synthesis + one per revise (max_retries=2 -> two revises)
    assert names.count("synthesized") == 3
    state = dict(events)["run_state"]
    assert state["approved"] is True
    assert state["attempts"] == 3


def test_pipeline_error_becomes_error_event(client, monkeypatch):
    client.install_fakes()

    async def boom(prompt, config, requester_role=None, on_result=None, on_progress=None):
        raise RuntimeError("model exploded")

    from agentic.state_manager import manager
    monkeypatch.setattr(manager, "plan_and_gather", boom)

    events = _run_events(client, _login(client)["access_token"])
    assert [name for name, _ in events] == ["run_started", "error"]
    assert "model exploded" in dict(events)["error"]["message"]
