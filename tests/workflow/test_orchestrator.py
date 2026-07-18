from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from vibe.workflow import orchestrator
from vibe.workflow.roles import RoleSpec, select_roles
from vibe.workflow.store import (
    compute_timings,
    initialize_database,
    publish_decision,
    read_status,
    read_status_snapshot,
    update_status,
)


class FakeProcess:
    def __init__(self, return_code: int, stdout: bytes, stderr: bytes) -> None:
        self._return_code = return_code
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code

    def terminate(self) -> None:
        self.returncode = self._return_code

    def kill(self) -> None:
        self.returncode = self._return_code


class HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._stopped = asyncio.Event()

    async def wait(self) -> int:
        await self._stopped.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._stopped.set()

    def kill(self) -> None:
        self.terminate()


def backend_role() -> RoleSpec:
    return next(role for role in select_roles(None) if role.name == "Backend")


def test_build_worker_command_targets_this_fork_with_role_profile(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project with spaces"
    prompt = 'Ship "quotes" & pipes | without a shell'

    command = orchestrator.build_worker_command(prompt, backend_role(), workdir)

    assert command == [
        sys.executable,
        "-m",
        "vibe",
        "--prompt",
        prompt,
        "--output",
        "streaming",
        "--trust",
        "--auto-approve",
        "--agent",
        "backend",
        "--max-price",
        "2.00",
        "--max-turns",
        "40",
        "--workdir",
        str(workdir),
    ]


def test_warm_start_brief_lists_goal_roles_and_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print()\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("", encoding="utf-8")

    brief = orchestrator.build_warm_start_brief(
        "Todo API", select_roles(None), tmp_path
    )

    assert "Team goal: Todo API" in brief
    assert "- Planner:" in brief and "- Frontend:" in brief
    assert "app.py" in brief
    assert ".hidden" not in brief
    assert "junk.js" not in brief


@pytest.mark.asyncio
async def test_block_unstarted_roles_marks_only_queued_roles_blocked(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "workflow.db"
    initialize_database(database_path)
    roles = select_roles(["Planner", "Backend", "Reviewer"])

    await orchestrator._initialize_role_statuses(database_path, roles)
    update_status(database_path, "Planner", "done", "Completed")
    update_status(database_path, "Backend", "working", "Implementing")

    await orchestrator._block_unstarted_roles(database_path, roles)

    statuses = {
        status["role"]: status for status in read_status_snapshot(database_path)
    }
    assert statuses["Planner"]["state"] == "done"
    assert statuses["Backend"]["state"] == "working"
    assert statuses["Reviewer"]["state"] == "blocked"
    assert statuses["Reviewer"]["current_task"] == (
        "Orchestrator cancelled before starting"
    )


def test_build_worker_prompt_injects_goal_brief_and_decisions(tmp_path: Path) -> None:
    db = tmp_path / "workflow.db"
    initialize_database(db)
    publish_decision(db, "Planner", "the plan", topic="plan")

    prompt = orchestrator.build_worker_prompt(
        backend_role(), "Todo API", "SHARED BRIEF", db
    )

    assert "Todo API" in prompt
    assert "SHARED BRIEF" in prompt
    assert "[plan]: the plan" in prompt
    assert "{{GOAL}}" not in prompt and "{{BRIEF}}" not in prompt


@pytest.mark.asyncio
async def test_run_worker_logs_only_valid_json_and_marks_nonzero_exit_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    database_path = workdir / "workflow.db"
    initialize_database(database_path)
    process = FakeProcess(
        return_code=7,
        stdout=(
            b'{"role":"assistant","content":"\xe2\x9c\x93"}\n'
            b"not-json\n"
            b"\n"
            b'{"role":"tool","content":"done"}\n'
        ),
        stderr=b"diagnostic from vibe\n",
    )
    invocation: dict[str, object] = {}
    status_updates: list[tuple[str, str, str]] = []

    async def fake_create_subprocess_exec(
        *command: str, **options: object
    ) -> FakeProcess:
        invocation["command"] = command
        invocation["options"] = options
        return process

    async def fake_set_status(
        _database_path: Path, role: str, state: str, current_task: str
    ) -> None:
        status_updates.append((role, state, current_task))

    monkeypatch.setattr(
        orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(orchestrator, "_set_status", fake_set_status)
    environment = {"WORKFLOW_TEST": "present"}

    result = await orchestrator.run_worker(
        backend_role(),
        "Implement the API",
        "",
        workdir,
        database_path,
        asyncio.get_running_loop().time() + 30,
        environment=environment,
    )

    assert result == orchestrator.WorkerResult(
        role="Backend", return_code=7, error="Vibe exited with code 7"
    )
    assert status_updates == [
        ("Backend", "working", "Working on: Implement the API"),
        ("Backend", "blocked", "Vibe exited with code 7"),
    ]
    command = invocation["command"]
    assert isinstance(command, tuple)
    assert command[0] == sys.executable
    assert command[command.index("--agent") + 1] == "backend"
    options = invocation["options"]
    assert isinstance(options, dict)
    env = options["env"]
    assert isinstance(env, dict)
    assert env["WORKFLOW_TEST"] == "present"
    assert env["VIBE_WORKFLOW_WORKER"] == "1"
    assert env["VIBE_WORKFLOW_DB"] == str(database_path)
    assert env["VIBE_WORKFLOW_ROLE"] == "Backend"

    json_lines = (
        (workdir / "logs" / "backend.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line) for line in json_lines] == [
        {"role": "assistant", "content": "✓"},
        {"role": "tool", "content": "done"},
    ]
    assert (workdir / "logs" / "backend.stderr.log").read_text(
        encoding="utf-8"
    ) == "diagnostic from vibe\n"


@pytest.mark.asyncio
async def test_run_worker_records_timing_events_from_ndjson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    database_path = workdir / "workflow.db"
    initialize_database(database_path)
    process = FakeProcess(
        return_code=0,
        stdout=(
            b'{"role":"system","content":"prompt"}\n'
            b'{"role":"assistant","content":"","tool_calls":[{"id":"1"}]}\n'
            b'{"role":"tool","content":"ok"}\n'
            b'{"role":"assistant","content":"","tool_calls":[{"id":"2"}]}\n'
            b'{"role":"assistant","content":"done","tool_calls":null}\n'
        ),
        stderr=b"",
    )

    async def fake_create_subprocess_exec(
        *_command: str, **_options: object
    ) -> FakeProcess:
        return process

    monkeypatch.setattr(
        orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    publish_decision(database_path, "Backend", "delivered")

    result = await orchestrator.run_worker(
        backend_role(),
        "Implement the API",
        "",
        workdir,
        database_path,
        asyncio.get_running_loop().time() + 30,
        environment={},
    )

    assert result.succeeded
    timing = compute_timings(database_path)["Backend"]
    assert timing["turns"] == 3
    assert timing["spawned_at"] is not None
    assert timing["first_action_s"] is not None
    assert timing["total_s"] is not None


@pytest.mark.asyncio
async def test_run_worker_terminates_and_blocks_at_global_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    database_path = workdir / "workflow.db"
    initialize_database(database_path)
    process = HangingProcess()
    status_updates: list[tuple[str, str, str]] = []

    async def fake_create_subprocess_exec(
        *_command: str, **_options: object
    ) -> HangingProcess:
        return process

    async def fake_set_status(
        _database_path: Path, role: str, state: str, current_task: str
    ) -> None:
        status_updates.append((role, state, current_task))

    monkeypatch.setattr(
        orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(orchestrator, "_set_status", fake_set_status)

    result = await orchestrator.run_worker(
        backend_role(),
        "Test the API",
        "",
        workdir,
        database_path,
        asyncio.get_running_loop().time() + 0.05,
        environment={},
    )

    assert result == orchestrator.WorkerResult(
        role="Backend", return_code=-1, timed_out=True
    )
    assert process.returncode == -15
    assert status_updates == [
        ("Backend", "working", "Working on: Test the API"),
        ("Backend", "blocked", "Global timeout reached"),
    ]


@pytest.mark.asyncio
async def test_teardown_crash_after_agent_reported_done_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    database_path = workdir / "workflow.db"
    initialize_database(database_path)
    # The agent finished its work (status done) but the vibe process died in
    # teardown with a nonzero exit — e.g. a leaked child process.
    process = FakeProcess(
        return_code=1,
        stdout=b'{"role":"assistant","content":"done","tool_calls":null}\n',
        stderr=b"RuntimeError: Event loop is closed\n",
    )

    async def fake_create_subprocess_exec(
        *_command: str, **_options: object
    ) -> FakeProcess:
        update_status(database_path, "Backend", "done", "Completed")
        return process

    monkeypatch.setattr(
        orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    result = await orchestrator.run_worker(
        backend_role(),
        "Implement the API",
        "",
        workdir,
        database_path,
        asyncio.get_running_loop().time() + 30,
        environment={},
    )

    assert result.succeeded
    status = read_status(database_path, "Backend")
    assert status is not None and status["state"] == "done"


@pytest.mark.asyncio
async def test_exit_zero_does_not_clobber_agent_reported_blocked(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "workflow.db"
    initialize_database(database_path)

    update_status(database_path, "Backend", "blocked", "waiting on credentials")
    await orchestrator._finalize_success(database_path, "Backend")
    status = read_status(database_path, "Backend")
    assert status is not None and status["state"] == "blocked"

    update_status(database_path, "Frontend", "working", "building")
    await orchestrator._finalize_success(database_path, "Frontend")
    status = read_status(database_path, "Frontend")
    assert status is not None and status["state"] == "done"


@pytest.mark.asyncio
async def test_successful_worker_is_blocked_when_it_publishes_no_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "workflow.db"
    initialize_database(database_path)
    status_updates: list[tuple[str, str, str]] = []

    async def fake_set_status(
        _database_path: Path, role: str, state: str, current_task: str
    ) -> None:
        status_updates.append((role, state, current_task))

    monkeypatch.setattr(orchestrator, "_set_status", fake_set_status)
    successful = orchestrator.WorkerResult(role="Backend", return_code=0)
    previous_id = int(
        publish_decision(database_path, "Backend", "Decision from an earlier run")
    )

    missing = await orchestrator._verify_published_decision(
        successful, database_path, previous_id
    )

    assert missing.error == "Backend exited successfully without publishing a decision"
    assert status_updates == [
        (
            "Backend",
            "blocked",
            "Backend exited successfully without publishing a decision",
        )
    ]

    publish_decision(database_path, "Backend", "Delivered the API")
    assert (
        await orchestrator._verify_published_decision(
            successful, database_path, previous_id
        )
        == successful
    )


@pytest.mark.asyncio
async def test_run_workflow_runs_waves_in_order_with_parallel_second_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    lifecycle: list[str] = []
    parallel_started: set[str] = set()
    both_parallel_workers_started = asyncio.Event()

    async def fake_run_worker(
        role: RoleSpec,
        _goal: str,
        brief: str,
        _workdir: Path,
        _database_path: Path,
        _deadline: float,
        *,
        environment: dict[str, str] | None = None,
    ) -> orchestrator.WorkerResult:
        assert brief == ""
        lifecycle.append(f"start:{role.name}")
        if role.name == "Planner":
            lifecycle.append("finish:Planner")
            return orchestrator.WorkerResult(
                role=role.name, return_code=3, error="planner failed"
            )

        parallel_started.add(role.name)
        if parallel_started == {"Backend", "Frontend"}:
            both_parallel_workers_started.set()
        await asyncio.wait_for(both_parallel_workers_started.wait(), timeout=1)
        lifecycle.append(f"finish:{role.name}")
        return orchestrator.WorkerResult(role=role.name, return_code=0)

    monkeypatch.setattr(orchestrator, "run_worker", fake_run_worker)

    async def fake_verify(
        result: orchestrator.WorkerResult, _database_path: Path, _since_id: int | None
    ) -> orchestrator.WorkerResult:
        return result

    monkeypatch.setattr(orchestrator, "_verify_published_decision", fake_verify)

    results = await orchestrator.run_workflow(
        "Build a Todo API", workdir, warm_start=False, environment={}
    )

    assert lifecycle[:2] == ["start:Planner", "finish:Planner"]
    assert set(lifecycle[2:4]) == {"start:Backend", "start:Frontend"}
    assert {result.role for result in results} == {"Planner", "Backend", "Frontend"}
    assert results[0].succeeded is False

    # The run reset happened: a fresh run row exists with the goal.
    from vibe.workflow.setup import workflow_database_path
    from vibe.workflow.store import current_run, read_status_snapshot

    database_path = workflow_database_path(workdir)
    run = current_run(database_path)
    assert run is not None and run["goal"] == "Build a Todo API"
    statuses = {
        status["role"]: status for status in read_status_snapshot(database_path)
    }
    assert statuses["Planner"]["state"] == "idle"
    assert statuses["Backend"]["state"] == "idle"
    assert statuses["Frontend"]["state"] == "idle"


def test_record_file_changes_attributes_via_claims(tmp_path: Path) -> None:
    from vibe.workflow.store import claim_file, read_changes

    database_path = tmp_path / "workflow.db"
    initialize_database(database_path)
    claim_file(database_path, "Frontend", "web/index.html")

    before = {"server/app.py": (1.0, 10), "old.txt": (1.0, 5)}
    after = {
        "server/app.py": (2.0, 30),
        "web/index.html": (2.0, 40),
        "server/auth.py": (2.0, 20),
    }
    orchestrator._record_file_changes(database_path, "Backend", before, after)

    changes = {(c["path"], c["action"]) for c in read_changes(database_path)}
    # web/index.html is claimed by Frontend -> not attributed to Backend.
    assert changes == {
        ("server/app.py", "modified"),
        ("server/auth.py", "created"),
        ("old.txt", "deleted"),
    }


def test_snapshot_files_skips_hidden_and_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / ".secret").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")

    snapshot = orchestrator._snapshot_files(tmp_path)
    assert set(snapshot) == {"app.py"}
