from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from vibe.workflow import server, store
from vibe.workflow.setup import workflow_database_path


def seeded_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "project"
    workdir.mkdir()
    db = workflow_database_path(workdir)
    store.initialize_database(db)
    store.start_run(db, "Todo API with JWT auth")
    store.update_status(db, "Planner", "done", "Plan published")
    store.update_status(db, "Backend", "working", "Implementing /auth/login")
    store.publish_decision(db, "Backend", "login returns JWT", topic="auth-contract")
    store.ask_question(db, "Frontend", "Backend", "Response shape?")
    store.claim_file(db, "Backend", "server/app.py")
    store.record_event(db, "Backend", "spawned")
    store.record_event(db, "Backend", "first_action")
    return workdir


def test_state_endpoint_serves_wire_shape(tmp_path: Path) -> None:
    workdir = seeded_workdir(tmp_path)
    client = TestClient(server.create_app(workdir))

    state = client.get("/state").json()

    assert state["goal"] == "Todo API with JWT auth"
    assert state["agents"]["Backend"]["status"] == "working"
    assert state["decisions"][0]["topic"] == "auth-contract"
    assert state["questions"][0]["from"] == "Frontend"
    assert state["claims"][0]["path"] == "server/app.py"
    assert state["timings"]["Backend"]["spawned_at"] is not None


def test_state_endpoint_before_any_run_is_empty(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    client = TestClient(server.create_app(workdir))

    assert client.get("/state").json() == {
        "goal": None,
        "run": None,
        "agents": {},
        "decisions": [],
        "questions": [],
        "claims": [],
        "timings": {},
    }
    assert client.get("/manifest").json() == {"project": None, "roles": []}


def test_manifest_lists_active_roles_with_dependencies(tmp_path: Path) -> None:
    workdir = seeded_workdir(tmp_path)
    client = TestClient(server.create_app(workdir))

    manifest = client.get("/manifest").json()

    assert manifest["project"]["goal"] == "Todo API with JWT auth"
    names = [role["name"] for role in manifest["roles"]]
    assert names == ["Planner", "Backend", "Frontend"]
    backend = next(role for role in manifest["roles"] if role["name"] == "Backend")
    assert backend["depends_on"] == ["Planner"]


def test_websocket_sends_immediate_snapshot(tmp_path: Path) -> None:
    workdir = seeded_workdir(tmp_path)
    client = TestClient(server.create_app(workdir))

    with client.websocket_connect("/ws") as websocket:
        first_frame = json.loads(websocket.receive_text())

    assert first_frame["goal"] == "Todo API with JWT auth"
    assert first_frame["agents"]["Planner"]["status"] == "done"


def test_root_serves_board_ui_or_build_hint(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    client = TestClient(server.create_app(workdir))

    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    # Committed board build -> the app shell; otherwise the build hint page.
    assert "MiaouFlow" in body or "root" in body
