from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import orchestrator
from workflow_memory.store import initialize_database, publish_decision


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


def test_build_worker_command_uses_programmatic_safety_limits_as_single_argv(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project with spaces"
    prompt = 'Ship "quotes" & pipes | without a shell'

    command = orchestrator.build_worker_command(prompt, workdir)

    assert command == [
        "vibe",
        "--prompt",
        prompt,
        "--output",
        "streaming",
        "--max-price",
        "1.00",
        "--max-turns",
        "25",
        "--agent",
        "auto-approve",
        "--auto-approve",
        "--workdir",
        str(workdir),
    ]


@pytest.mark.asyncio
async def test_run_worker_logs_only_valid_json_and_marks_nonzero_exit_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    database_path = workdir / "workflow.db"
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
    monkeypatch.setattr(orchestrator, "build_worker_prompt", lambda role, goal: goal)
    environment = {"MISTRAL_API_KEY": "test-key", "WORKFLOW_TEST": "present"}

    result = await orchestrator.run_worker(
        "Backend",
        "Implement the API",
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
    assert command[0] == "vibe"
    assert command[command.index("--prompt") + 1] == "Implement the API"
    assert command[command.index("--workdir") + 1] == str(workdir)
    options = invocation["options"]
    assert isinstance(options, dict)
    assert options["env"] == {
        **environment,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "VIBE_WORKFLOW_WORKER": "1",
    }

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
async def test_run_worker_terminates_and_blocks_at_global_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
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
    monkeypatch.setattr(orchestrator, "build_worker_prompt", lambda role, goal: goal)

    result = await orchestrator.run_worker(
        "QA",
        "Test the API",
        workdir,
        workdir / "workflow.db",
        asyncio.get_running_loop().time() + 0.05,
        environment={"MISTRAL_API_KEY": "test-key"},
    )

    assert result == orchestrator.WorkerResult(
        role="QA", return_code=-1, timed_out=True
    )
    assert process.returncode == -15
    assert status_updates == [
        ("QA", "working", "Working on: Test the API"),
        ("QA", "blocked", "Global timeout reached"),
    ]


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

    assert missing == orchestrator.WorkerResult(
        role="Backend",
        return_code=0,
        error="Backend exited successfully without publishing a decision",
    )
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
async def test_run_workflow_starts_parallel_workers_after_planner_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    lifecycle: list[str] = []
    parallel_started: set[str] = set()
    both_parallel_workers_started = asyncio.Event()

    async def fake_run_worker(
        role: str,
        _goal: str,
        _workdir: Path,
        _database_path: Path,
        _deadline: float,
        *,
        environment: dict[str, str] | None = None,
    ) -> orchestrator.WorkerResult:
        assert environment == {"MISTRAL_API_KEY": "test-key"}
        lifecycle.append(f"start:{role}")
        if role == "Planner":
            lifecycle.append("finish:Planner")
            return orchestrator.WorkerResult(
                role=role, return_code=3, error="planner failed"
            )

        parallel_started.add(role)
        if parallel_started == {"Backend", "QA"}:
            both_parallel_workers_started.set()
        await asyncio.wait_for(both_parallel_workers_started.wait(), timeout=1)
        lifecycle.append(f"finish:{role}")
        return orchestrator.WorkerResult(role=role, return_code=0)

    configured: list[Path] = []
    initialized: list[Path] = []
    monkeypatch.setattr(
        orchestrator,
        "configure_workdir",
        lambda path: configured.append(path) or path / ".vibe" / "config.toml",
    )
    monkeypatch.setattr(
        orchestrator, "initialize_database", lambda path: initialized.append(Path(path))
    )
    monkeypatch.setattr(orchestrator, "run_worker", fake_run_worker)

    async def fake_verify(
        result: orchestrator.WorkerResult, _database_path: Path, _since_id: int | None
    ) -> orchestrator.WorkerResult:
        return result

    monkeypatch.setattr(orchestrator, "_verify_published_decision", fake_verify)

    async def fake_latest_decision_id(_database_path: Path) -> None:
        return None

    monkeypatch.setattr(orchestrator, "_latest_decision_id", fake_latest_decision_id)

    results = await orchestrator.run_workflow(
        "Build a Todo API", workdir, environment={"MISTRAL_API_KEY": "test-key"}
    )

    resolved_workdir = workdir.resolve()
    assert configured == [resolved_workdir]
    assert initialized == [resolved_workdir / "workflow.db"]
    assert lifecycle[:2] == ["start:Planner", "finish:Planner"]
    assert set(lifecycle[2:4]) == {"start:Backend", "start:QA"}
    assert {result.role for result in results} == {"Planner", "Backend", "QA"}
    assert results[0].succeeded is False
    assert results[1].succeeded is True
    assert results[2].succeeded is True
