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
        "messages": [],
        "broadcasts": [],
        "changes": [],
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


def test_logs_endpoint_reads_role_transcript(tmp_path: Path) -> None:
    workdir = seeded_workdir(tmp_path)
    logs_dir = workdir / "logs"
    logs_dir.mkdir()
    (logs_dir / "backend.jsonl").write_text(
        '{"role":"system","content":"hidden"}\n'
        '{"role":"assistant","content":"working","tool_calls":'
        '[{"function":{"name":"claim_file"}}]}\n'
        '{"role":"tool","content":"ok","name":"claim_file"}\n',
        encoding="utf-8",
    )
    client = TestClient(server.create_app(workdir))

    payload = client.get("/logs", params={"role": "Backend"}).json()

    assert [e["role"] for e in payload["entries"]] == ["assistant", "tool"]
    assert payload["entries"][0]["tools"] == ["claim_file"]
    assert client.get("/logs").status_code == 400


def test_system_endpoint_lists_provisioning(tmp_path: Path) -> None:
    from vibe.workflow.roles import select_roles
    from vibe.workflow.setup import configure_workdir

    workdir = seeded_workdir(tmp_path)
    configure_workdir(workdir, select_roles(None))
    client = TestClient(server.create_app(workdir))

    system = client.get("/system").json()

    profile_names = {p["name"] for p in system["agent_profiles"]}
    assert {"planner", "backend", "frontend"} <= profile_names
    tool_names = {t["name"] for t in system["blackboard_tools"]}
    assert {"publish_decision", "send_message", "read_inbox"} <= tool_names
    assert "planner" in system["role_prompts"]


def test_post_agent_registers_custom_role(tmp_path: Path) -> None:
    from vibe.workflow.roles import select_roles

    workdir = seeded_workdir(tmp_path)
    client = TestClient(server.create_app(workdir))

    response = client.post(
        "/agents",
        json={"name": "Security", "objective": "Audit auth and input validation"},
    )

    assert response.status_code == 201
    roles = select_roles(None, workdir=workdir)
    names = [role.name for role in roles]
    assert "Security" in names
    security = next(role for role in roles if role.name == "Security")
    assert security.depends_on == ("Planner",)
    # It shows up in the manifest for the graph.
    manifest_names = [r["name"] for r in client.get("/manifest").json()["roles"]]
    assert "Security" in manifest_names
    # Validation errors are surfaced.
    assert client.post("/agents", json={"name": ""}).status_code == 400


def test_change_endpoint_serves_diff(tmp_path: Path) -> None:
    from vibe.workflow.store import record_change

    workdir = seeded_workdir(tmp_path)
    record_change(
        workflow_database_path(workdir),
        "Backend",
        "app.py",
        "modified",
        diff="--- a/app.py\n+++ b/app.py\n+new\n",
    )
    client = TestClient(server.create_app(workdir))

    changes = client.get("/state").json()["changes"]
    target = next(c for c in changes if c["path"] == "app.py")
    payload = client.get("/change", params={"id": str(target["id"])}).json()
    assert "+new" in payload["diff"]
    assert client.get("/change", params={"id": "abc"}).status_code == 400
    assert client.get("/change", params={"id": "999"}).status_code == 404
