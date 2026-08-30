"""The permission ledger (Michael).

The gates were already correct; what they lacked was a record. These pin
that the record is written, is attributed to the right principal, is not
browsable by another one, and cannot itself break a run.
"""

import json

import pytest
from fastapi.testclient import TestClient

from agentic import audit
from agentic.contracts.config import load_config
from agentic.contracts.messages import GatheredFile, GatherRequest, GatherResult
from agentic.gatherers import permissions, spawn
from agentic.gatherers.gather import _gate
from agentic.server import auth
from agentic.server.app import create_app


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENTIC_AUDIT_LOG", str(path))
    return path


def _lines(path):
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---- context gating ----

def test_nothing_is_written_outside_a_run(ledger):
    """The ledger records runs. A unit test calling a gate directly must not
    leave lines in it, or the file stops meaning anything."""
    audit.record(audit.ALLOW, department="engineering", path="roadmap.md", stage="x")
    assert _lines(ledger) == []


def test_context_attributes_every_decision(ledger):
    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        audit.record(audit.DENY, department="hr", path="comp-bands.md", stage="pre-read")

    entry = _lines(ledger)[0]
    assert entry["run_id"] == "r1"
    assert entry["principal"] == "alice"
    assert entry["role"] == "analyst"
    assert entry["decision"] == "deny"
    assert entry["department"] == "hr"
    assert entry["ts"]


def test_context_does_not_leak_past_the_block(ledger):
    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        pass
    audit.record(audit.ALLOW, department="engineering", path="roadmap.md", stage="x")
    assert _lines(ledger) == []


async def test_context_reaches_decisions_made_in_spawned_tasks(ledger):
    """Gatherers run under asyncio.gather; the ledger has to follow them in."""
    import asyncio

    async def decide(n):
        audit.record(audit.ALLOW, department="engineering", path=f"{n}.md", stage="x")

    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        await asyncio.gather(*[decide(i) for i in range(3)])

    assert len(_lines(ledger)) == 3
    assert {e["run_id"] for e in _lines(ledger)} == {"r1"}


# ---- the real gates write to it ----

def test_the_pre_read_gate_records_both_outcomes(ledger):
    config = load_config("config/environment.example.yaml")
    perms = permissions.load_permissions_config(
        "config/permissions.example.yaml", principal_role="analyst"
    )
    result = GatherResult(request_id="r0", department="engineering")
    request = GatherRequest(request_id="r0", department="engineering", query="q")

    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        _gate(request, config, perms, result)

    entries = _lines(ledger)
    assert entries and all(e["stage"] == "pre-read" for e in entries)
    assert any(e["decision"] == "allow" for e in entries)


async def test_the_spawn_gate_records_denials_with_a_reason(ledger, monkeypatch):
    config = load_config("config/environment.example.yaml")
    perms = permissions.load_permissions_config(
        "config/permissions.example.yaml", principal_role="analyst"
    )

    async def fake(request, cfg, p, on_progress=None):
        return GatherResult(
            request_id=request.request_id,
            department="engineering",
            files=[
                GatheredFile(path="roadmap.md", department="engineering", content="x"),
                GatheredFile(path="hr/comp-bands.md", department="engineering", content="x"),
            ],
        )

    monkeypatch.setattr(spawn.gather, "gather", fake)
    request = GatherRequest(request_id="r0", department="engineering", query="q")
    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        await spawn.spawn_gatherers([request], config, permissions_cfg=perms)

    gate = [e for e in _lines(ledger) if e["stage"] == "spawn-gate"]
    denied = [e for e in gate if e["decision"] == "deny"]
    assert [e["path"] for e in denied] == ["hr/comp-bands.md"]
    assert denied[0]["reason"] == "no such file in this department"


# ---- reading it ----

def test_read_filters_by_principal_and_returns_newest_first(ledger):
    for who in ("alice", "root", "alice"):
        with audit.run_context(run_id="r", principal=who, role="x"):
            audit.record(audit.ALLOW, department="engineering", path=who, stage="s")

    mine = audit.read(principal="alice", path=ledger)
    assert [e["path"] for e in mine] == ["alice", "alice"]
    assert audit.read(path=ledger)[0]["path"] == "alice"  # newest first


def test_a_torn_line_does_not_sink_the_read(ledger):
    with audit.run_context(run_id="r", principal="alice", role="x"):
        audit.record(audit.ALLOW, department="engineering", path="a.md", stage="s")
    with ledger.open("a") as fh:
        fh.write('{"partial": ')  # a write cut off mid-flight

    assert [e["path"] for e in audit.read(path=ledger)] == ["a.md"]


def test_record_never_raises_even_when_the_log_cannot_be_written(monkeypatch, tmp_path):
    """A ledger that can take down a run is worse than a gap in the ledger —
    the decision it is recording has already been enforced."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x")
    monkeypatch.setenv("AGENTIC_AUDIT_LOG", str(blocked / "audit.jsonl"))

    with audit.run_context(run_id="r", principal="alice", role="x"):
        audit.record(audit.ALLOW, department="engineering", path="a.md", stage="s")


def test_summarise_counts_outcomes(ledger):
    with audit.run_context(run_id="r1", principal="alice", role="x"):
        audit.record(audit.ALLOW, department="engineering", path="a", stage="s")
        audit.record(audit.DENY, department="hr", path="b", stage="s")

    summary = audit.summarise(audit.read(path=ledger))
    assert summary == {
        "total": 2, "allowed": 1, "denied": 1,
        "departments": ["engineering", "hr"], "runs": 1,
    }


# ---- HTTP ----

@pytest.fixture
def client(monkeypatch, tmp_path, ledger):
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
    monkeypatch.setenv("AGENTIC_RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.setenv("AGENTIC_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr(
        "agentic.gatherers.permissions.DEFAULT_PERMISSIONS_CONFIG",
        "config/permissions.example.yaml",
    )
    return TestClient(create_app())


def _token(client, username, password):
    res = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_audit_requires_auth(client):
    assert client.get("/api/audit").status_code == 401


def test_audit_shows_only_the_callers_own_decisions(client, ledger):
    with audit.run_context(run_id="r1", principal="alice", role="analyst"):
        audit.record(audit.DENY, department="hr", path="comp-bands.md", stage="pre-read")
    with audit.run_context(run_id="r2", principal="root", role="admin"):
        audit.record(audit.ALLOW, department="hr", path="secret-plan.md", stage="pre-read")

    body = client.get("/api/audit", headers=_token(client, "alice", "wonderland")).json()

    assert [e["path"] for e in body["entries"]] == ["comp-bands.md"]
    assert body["summary"] == {
        "total": 1, "allowed": 0, "denied": 1, "departments": ["hr"], "runs": 1,
    }
    # another principal's line must not be reachable through this endpoint
    assert "secret-plan.md" not in client.get(
        "/api/audit", headers=_token(client, "alice", "wonderland")
    ).text
