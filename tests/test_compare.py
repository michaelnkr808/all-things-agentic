"""Side-by-side role comparison (Michael).

The feature is one prompt run under two roles. The risk is that a feature
which takes a role name is a privilege escalation wearing a UI, so most of
what is pinned here is the direction of the comparison.
"""

import pytest
from fastapi.testclient import TestClient

from agentic.gatherers.permissions import load_permissions_config
from agentic.server import auth, compare
from agentic.server.app import create_app
from agentic.server.sse import parse_stream

PERMS = "config/permissions.example.yaml"


@pytest.fixture
def perms():
    return load_permissions_config(PERMS, principal_role="admin")


# ---- direction ----

def test_admin_may_compare_down_to_analyst(perms):
    assert compare.comparable_roles("admin", perms) == ["analyst"]


def test_analyst_may_not_compare_up(perms):
    assert compare.comparable_roles("analyst", perms) == []
    with pytest.raises(compare.CompareError, match="strictly fewer"):
        compare.check_comparable("analyst", "admin", perms)


def test_a_role_cannot_compare_against_itself(perms):
    """Two identical runs cost twice as much to learn nothing."""
    assert "admin" not in compare.comparable_roles("admin", perms)
    with pytest.raises(compare.CompareError):
        compare.check_comparable("admin", "admin", perms)


def test_unknown_role_is_refused(perms):
    with pytest.raises(compare.CompareError, match="unknown role"):
        compare.check_comparable("admin", "root-admin-9000", perms)


def test_equal_scope_roles_are_not_comparable():
    """A role with the same departments under a different name is not narrower."""
    perms = load_permissions_config(PERMS, principal_role="admin")
    perms.roles["auditor"] = perms.roles["admin"].model_copy(deep=True)
    assert "auditor" not in compare.comparable_roles("admin", perms)


# ---- the diff ----

def test_diff_separates_what_each_side_reached():
    yours = {
        "approved": True, "attempts": 1, "obsidian_markdown": "full answer",
        "sources": ["engineering/roadmap.md", "hr/comp-bands.md"],
        "departments_used": ["engineering", "hr"], "citations": {"integrity": 1.0},
    }
    theirs = {
        "approved": True, "attempts": 2, "obsidian_markdown": "partial answer",
        "sources": ["engineering/roadmap.md"],
        "departments_used": ["engineering"], "citations": {"integrity": 1.0},
    }
    diff = compare.build_diff(yours, theirs, "admin", "analyst", [], ["comp-bands.md"])

    assert diff["only_yours"] == ["hr/comp-bands.md"]
    assert diff["only_theirs"] == []
    assert diff["shared"] == ["engineering/roadmap.md"]
    assert diff["yours"]["role"] == "admin"
    assert diff["theirs"]["denied"] == ["comp-bands.md"]
    assert diff["theirs"]["attempts"] == 2


async def test_run_comparison_tags_every_event_with_its_side():
    events = []

    async def execute(prompt, role, *, emit, **kwargs):
        emit("gatherer_result", {"department": "hr", "denied": ["comp-bands.md"]})
        return {"sources": [f"{role}/f.md"], "departments_used": [], "approved": True}

    await compare.run_comparison(
        "q", "admin", "analyst", execute=execute, emit=lambda e, p: events.append((e, p))
    )

    names = [n for n, _ in events]
    assert names[0] == "compare_started"
    assert names[-1] == "compare_result"
    sides = {p["side"] for n, p in events if n == "gatherer_result"}
    assert sides == {"yours", "theirs"}
    # denials are accumulated per side from the tagged events
    result = dict(events)["compare_result"]
    assert result["theirs"]["denied"] == ["comp-bands.md"]


async def test_a_failing_side_is_reported_and_reraised():
    events = []

    async def execute(prompt, role, *, emit, **kwargs):
        if role == "analyst":
            raise RuntimeError("model exploded")
        return {"sources": [], "departments_used": [], "approved": True}

    with pytest.raises(RuntimeError, match="model exploded"):
        await compare.run_comparison(
            "q", "admin", "analyst", execute=execute,
            emit=lambda e, p: events.append((e, p)),
        )

    names = [n for n, _ in events]
    assert "error" in names and "compare_failed" in names
    assert "compare_result" not in names


# ---- HTTP ----

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_JWT_SECRET", "test-secret")
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n"
        "  alice:\n    role: analyst\n"
        f"    password_hash: {auth.hash_password('wonderland')}\n"
        "  root:\n    role: admin\n"
        f"    password_hash: {auth.hash_password('toor')}\n"
    )
    monkeypatch.setenv("AGENTIC_USERS_PATH", str(users))
    monkeypatch.setenv("AGENTIC_CONFIG_PATH", "config/environment.example.yaml")
    monkeypatch.setenv("AGENTIC_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("AGENTIC_RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.setenv("AGENTIC_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr(
        "agentic.gatherers.permissions.DEFAULT_PERMISSIONS_CONFIG", PERMS
    )
    return TestClient(create_app())


def _token(client, username, password):
    res = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_compare_requires_auth(client):
    res = client.post("/api/compare", json={"prompt": "q", "as_role": "analyst"})
    assert res.status_code == 401


def test_analyst_asking_to_compare_as_admin_is_403(client):
    """The escalation this endpoint would otherwise be."""
    res = client.post(
        "/api/compare",
        json={"prompt": "q", "as_role": "admin"},
        headers=_token(client, "alice", "wonderland"),
    )
    assert res.status_code == 403
    assert "strictly fewer" in res.json()["detail"]


def test_fleet_advertises_only_downward_comparisons(client):
    admin = client.get("/api/fleet", headers=_token(client, "root", "toor")).json()
    analyst = client.get(
        "/api/fleet", headers=_token(client, "alice", "wonderland")
    ).json()

    assert admin["compare_roles"] == ["analyst"]
    assert analyst["compare_roles"] == []


def test_admin_comparison_streams_both_sides(client, monkeypatch):
    """Faked at the pipeline seam — this pins the HTTP surface, not the models."""

    async def fake_execute(prompt, role, emit, config_path=None, config=None):
        sources = ["engineering/roadmap.md"]
        if role == "admin":
            sources.append("hr/comp-bands.md")
        else:
            emit("gatherer_result", {"department": "hr", "denied": ["comp-bands.md"]})
        state = {
            "approved": True, "attempts": 1, "obsidian_markdown": f"answer as {role}",
            "sources": sources, "departments_used": ["engineering"], "citations": None,
        }
        emit("run_state", state)
        return state

    monkeypatch.setattr("agentic.server.runs.execute_run", fake_execute)
    res = client.post(
        "/api/compare",
        json={"prompt": "q", "as_role": "analyst"},
        headers=_token(client, "root", "toor"),
    )
    assert res.status_code == 200

    events = parse_stream(res.text)
    names = [n for n, _ in events]
    assert names[0] == "compare_started"
    assert names[-1] == "compare_result"

    result = dict(events)["compare_result"]
    assert result["only_yours"] == ["hr/comp-bands.md"]
    assert result["theirs"]["denied"] == ["comp-bands.md"]
    assert result["yours"]["answer"] == "answer as admin"
    assert result["theirs"]["answer"] == "answer as analyst"
