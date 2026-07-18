from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import initialize_database, publish_decision


def run_hook(payload: dict) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "vibe.workflow.hooks.guard_push"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.returncode, completed.stdout.strip()


def payload(workdir: Path, command: str) -> dict:
    return {
        "hook_event_name": "pre_tool",
        "tool_name": "bash",
        "tool_call_id": "t1",
        "tool_input": {"command": command},
        "cwd": str(workdir),
        "session_id": "s",
    }


def seeded(tmp_path: Path, verdict: str | None) -> Path:
    db = workflow_database_path(tmp_path)
    initialize_database(db)
    if verdict is not None:
        publish_decision(db, "Reviewer", verdict, topic="review-verdict")
    return tmp_path


def test_push_denied_without_any_verdict(tmp_path: Path) -> None:
    workdir = seeded(tmp_path, None)
    code, out = run_hook(payload(workdir, "git push origin main"))
    assert code == 0
    assert json.loads(out)["decision"] == "deny"


def test_push_denied_on_no_go(tmp_path: Path) -> None:
    workdir = seeded(tmp_path, "NO-GO: tests are red")
    code, out = run_hook(payload(workdir, "git push"))
    assert code == 0
    response = json.loads(out)
    assert response["decision"] == "deny"
    assert "NO-GO" in response["reason"]


def test_push_allowed_on_go(tmp_path: Path) -> None:
    workdir = seeded(tmp_path, "GO: contracts honored, suite green")
    code, out = run_hook(payload(workdir, "git push origin main"))
    assert code == 0
    assert out == ""


def test_non_push_commands_pass_through(tmp_path: Path) -> None:
    workdir = seeded(tmp_path, "NO-GO: red")
    code, out = run_hook(payload(workdir, "git status"))
    assert (code, out) == (0, "")


def test_garbage_stdin_fails_open() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "vibe.workflow.hooks.guard_push"],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == ""
