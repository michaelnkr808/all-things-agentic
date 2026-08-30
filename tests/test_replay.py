"""Recorded runs and their replay (Michael).

Replay exists so a demo survives a dead API key or a spent quota. That makes
it a second way into the same data, so most of what is pinned here is that it
is not a way *around* anything: the role a run was recorded as is the role it
replays to, the recording files are not on the static mount, and a name is a
slug rather than a path.
"""

import json

import pytest
from fastapi.testclient import TestClient

from agentic.server import auth, replay
from agentic.server.app import create_app
from agentic.server.sse import parse_stream

SECRET = "test-secret"


def _events(role="admin"):
    return [
        {"t": 0.0, "event": "run_started", "payload": {"prompt": "q", "run_id": "old"}},
        {"t": 0.2, "event": "gatherer_result",
         "payload": {"department": "engineering", "kept": 1, "denied": [], "errors": []}},
        {"t": 0.4, "event": "synthesized", "payload": {"revision": 0}},
        {"t": 0.5, "event": "veto", "payload": {"approved": True, "reasons": []}},
        {"t": 0.6, "event": "run_state", "payload": {
            "approved": True, "attempts": 1, "obsidian_markdown": "answer",
            "viz_path": "/home/somebody/out/graphs/run.html",
            "viz_url": "/graphs/run.html", "graph": {"nodes": [], "edges": []},
        }},
    ]


def write_recording(directory, name="demo-admin", role="admin", prompt="q", page=False):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({
        "version": 1, "name": name, "prompt": prompt, "role": role,
        "recorded_at": "2026-08-29T00:00:00+00:00", "duration": 0.6,
        "events": _events(role),
    }))
    if page:
        pages = replay.pages_dir(directory)
        pages.mkdir(parents=True, exist_ok=True)
        (pages / f"{name}.html").write_text("<html>graph</html>")
    return name


# ---- names are slugs, never paths ----

@pytest.mark.parametrize("name", [
    "../secrets", "a/b", "..", ".", "", "with space", "UPPER", "-leading",
    "a" * 65, "demo.json", "/etc/passwd",
])
def test_bad_names_are_refused_before_the_filesystem(name, tmp_path):
    with pytest.raises(replay.ReplayError):
        replay.load_recording(name, tmp_path)


def test_good_names_are_accepted(tmp_path):
    write_recording(tmp_path, name="q3-spend-admin")
    assert replay.load_recording("q3-spend-admin", tmp_path)["role"] == "admin"


# ---- recorder ----

def test_recorder_wraps_emit_and_forwards_unchanged():
    seen = []
    rec = replay.Recorder(prompt="q", role="admin", name="x")
    emit = rec.wrap(lambda e, p: seen.append((e, p)))
    emit("run_started", {"prompt": "q"})
    emit("veto", {"approved": False})

    assert seen == [("run_started", {"prompt": "q"}), ("veto", {"approved": False})]
    assert [e["event"] for e in rec.events] == ["run_started", "veto"]
    assert rec.events[0]["t"] == 0.0  # timestamps are relative to the first event


def test_recorder_save_round_trips(tmp_path):
    rec = replay.Recorder(prompt="q", role="analyst", name="demo-analyst")
    rec.emit("run_started", {"prompt": "q"})
    rec.emit("run_state", {"approved": True})
    rec.save(tmp_path)

    data = replay.load_recording("demo-analyst", tmp_path)
    assert data["role"] == "analyst"
    assert data["prompt"] == "q"
    assert [e["event"] for e in data["events"]] == ["run_started", "run_state"]


def test_recorder_copies_the_graph_page_into_pages_subdir(tmp_path):
    graph = tmp_path / "run.html"
    graph.write_text("<html>graph</html>")
    rec = replay.Recorder(prompt="q", role="admin", name="demo-admin")
    rec.emit("run_state", {"viz_path": str(graph)})
    rec.save(tmp_path / "recordings")

    page = replay.pages_dir(tmp_path / "recordings") / "demo-admin.html"
    assert page.read_text() == "<html>graph</html>"


def test_unique_name_does_not_overwrite(tmp_path):
    first = replay.unique_name("Q3 spend?", "admin", tmp_path)
    assert first == "q3-spend-admin"
    write_recording(tmp_path, name=first)
    assert replay.unique_name("Q3 spend?", "admin", tmp_path) == "q3-spend-admin-2"


# ---- payload rewriting ----

def test_replay_drops_local_paths_and_repoints_viz_url(tmp_path):
    write_recording(tmp_path, page=True)
    data = replay.load_recording("demo-admin", tmp_path)
    state = [p for _, e, p in replay.replay_events(data, "demo-admin", tmp_path)
             if e == "run_state"][0]

    assert "viz_path" not in state  # the recorder's filesystem is not the client's
    assert state["viz_url"] == "/replays/demo-admin.html"
    assert state["replay"] is True
    assert state["graph"] == {"nodes": [], "edges": []}


def test_missing_graph_page_nulls_the_link_but_keeps_the_graph(tmp_path):
    write_recording(tmp_path, page=False)
    data = replay.load_recording("demo-admin", tmp_path)
    state = [p for _, e, p in replay.replay_events(data, "demo-admin", tmp_path)
             if e == "run_state"][0]

    assert state["viz_url"] is None
    assert state["graph"] == {"nodes": [], "edges": []}


def test_long_gaps_collapse_and_speed_is_clamped(tmp_path):
    name = write_recording(tmp_path)
    data = replay.load_recording(name, tmp_path)
    data["events"][1]["t"] = 30.0  # a live planner call nobody wants to re-watch

    gaps = [g for g, _, _ in replay.replay_events(data, name, tmp_path)]
    assert max(gaps) <= replay.MAX_GAP

    fast = [g for g, _, _ in replay.replay_events(data, name, tmp_path, speed=1000)]
    assert max(fast) <= replay.MAX_GAP / replay.MAX_SPEED


# ---- HTTP surface ----

@pytest.fixture
def client(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        "agentic.gatherers.permissions.DEFAULT_PERMISSIONS_CONFIG",
        "config/permissions.example.yaml",
    )
    recordings = tmp_path / "recordings"
    monkeypatch.setenv("AGENTIC_RECORDINGS_DIR", str(recordings))
    monkeypatch.setenv("AGENTIC_GRAPHS_DIR", str(tmp_path / "graphs"))
    client = TestClient(create_app())
    client.recordings = recordings
    yield client


def _token(client, username="root", password="toor"):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_replays_require_auth(client):
    assert client.get("/api/replays").status_code == 401
    assert client.post("/api/replay/demo-admin").status_code == 401


def test_listing_marks_which_recordings_this_role_can_play(client):
    write_recording(client.recordings, name="demo-admin", role="admin")
    write_recording(client.recordings, name="demo-analyst", role="analyst")

    body = client.get("/api/replays", headers=_token(client, "alice", "wonderland")).json()
    assert body["role"] == "analyst"
    by_name = {r["name"]: r for r in body["replays"]}
    assert by_name["demo-analyst"]["playable"] is True
    assert by_name["demo-admin"]["playable"] is False
    # metadata only — a listing must not hand over the recorded contents
    assert "events" not in str(by_name["demo-admin"].get("payload", ""))
    assert by_name["demo-admin"]["events"] == 5  # a count, not the events


def test_analyst_cannot_replay_an_admin_recording(client):
    write_recording(client.recordings, name="demo-admin", role="admin")
    res = client.post("/api/replay/demo-admin",
                      headers=_token(client, "alice", "wonderland"))
    assert res.status_code == 403
    assert "admin" in res.json()["detail"]
    assert "obsidian_markdown" not in res.text


def test_matching_role_replays_the_recorded_sequence(client):
    write_recording(client.recordings, name="demo-admin", role="admin", page=True)
    res = client.post("/api/replay/demo-admin?speed=8", headers=_token(client))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_stream(res.text)
    assert [n for n, _ in events] == [
        "run_started", "gatherer_result", "synthesized", "veto", "run_state"
    ]
    by_name = dict(events)
    assert by_name["run_started"]["run_id"] != "old"  # a fresh id, cancellable
    assert by_name["run_started"]["replay"] is True  # flagged from frame one
    assert by_name["run_state"]["obsidian_markdown"] == "answer"
    assert by_name["run_state"]["viz_url"] == "/replays/demo-admin.html"


def test_unknown_recording_is_404(client):
    assert client.post("/api/replay/nope", headers=_token(client)).status_code == 404


def test_recording_json_is_not_on_the_static_mount(client):
    """The role check must not be skippable by asking for the file."""
    write_recording(client.recordings, name="demo-admin", role="admin", page=True)
    analyst = _token(client, "alice", "wonderland")

    for path in ("/replays/demo-admin.json", "/replays/../demo-admin.json"):
        res = client.get(path, headers=analyst)
        assert res.status_code != 200, path
        assert "obsidian_markdown" not in res.text

    # the graph page itself is served, matching /graphs for live runs
    assert client.get("/replays/demo-admin.html").status_code == 200
