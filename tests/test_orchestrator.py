from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

import orchestrator
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.models import MCPStdio
from vibe.core.config.vibe_schema import VibeConfigSchema
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


def test_build_worker_command_uses_role_limits_and_restricted_tools(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project with spaces"
    prompt = 'Ship "quotes" & pipes | without a shell'

    command = orchestrator.build_worker_command("Planner", prompt, workdir)

    assert command == [
        sys.executable,
        "-m",
        "vibe.cli.entrypoint",
        "--prompt",
        prompt,
        "--output",
        "streaming",
        "--max-price",
        "5.00",
        "--max-turns",
        "50",
        "--agent",
        "auto-approve",
        "--auto-approve",
        "--workdir",
        str(workdir),
        "--enabled-tools",
        "read_file",
        "--enabled-tools",
        "grep",
        "--enabled-tools",
        "workflow_read_decisions",
        "--enabled-tools",
        "workflow_publish_decision",
        "--enabled-tools",
        "workflow_update_status",
    ]

    backend_command = orchestrator.build_worker_command("Backend", prompt, workdir)
    qa_command = orchestrator.build_worker_command("QA", prompt, workdir)

    assert backend_command[backend_command.index("--max-turns") + 1] == "50"
    assert qa_command[qa_command.index("--max-turns") + 1] == "50"
    for command in (backend_command, qa_command):
        assert "bash" in command
        assert "write_file" in command
        assert "workflow_publish_decision" in command


def test_completed_worker_succeeds_even_with_nonzero_process_exit() -> None:
    result = orchestrator.WorkerResult(role="Planner", return_code=1, completed=True)

    assert result.succeeded is True


def test_build_worker_environment_adds_runtime_mcp_without_project_files(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "workflow.db"
    existing_server = {
        "name": "docs",
        "transport": "http",
        "url": "https://example.test/mcp",
    }

    environment = orchestrator.build_worker_environment(
        {
            "VIBE_ACTIVE_MODEL": "devstral-small",
            "VIBE_MCP_SERVERS": json.dumps([existing_server]),
            "VIRTUAL_ENV": str(tmp_path / ".venv"),
        },
        database_path,
    )

    assert environment["VIBE_ACTIVE_MODEL"] == "mistral-medium-3.5"
    assert "VIRTUAL_ENV" not in environment
    assert json.loads(environment["VIBE_MCP_SERVERS"]) == [
        existing_server,
        {
            "name": "workflow",
            "transport": "stdio",
            "command": [sys.executable],
            "args": ["-m", "workflow_memory.server"],
            "env": {"WORKFLOW_DB": str(database_path)},
        },
    ]
    assert not (tmp_path / ".vibe").exists()


def test_build_worker_environment_rejects_invalid_mcp_json(tmp_path: Path) -> None:
    with pytest.raises(
        orchestrator.WorkflowRunError, match="VIBE_MCP_SERVERS must contain valid JSON"
    ):
        orchestrator.build_worker_environment(
            {"VIBE_MCP_SERVERS": "not-json"}, tmp_path / "workflow.db"
        )


@pytest.mark.asyncio
async def test_worker_mcp_environment_is_valid_vibe_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = orchestrator.build_worker_environment({}, tmp_path / "workflow.db")
    monkeypatch.setenv("VIBE_MCP_SERVERS", environment["VIBE_MCP_SERVERS"])

    raw_config = await EnvironmentLayer(schema=VibeConfigSchema).load()
    config = VibeConfigSchema.model_validate(raw_config.model_dump())

    assert config.active_model == "mistral-medium-3.5"
    assert len(config.mcp_servers) == 1
    server = config.mcp_servers[0]
    assert isinstance(server, MCPStdio)
    assert server.name == "workflow"
    assert server.argv() == [sys.executable, "-m", "workflow_memory.server"]
    assert server.env == {"WORKFLOW_DB": str(tmp_path / "workflow.db")}


@pytest.mark.asyncio
async def test_run_worker_logs_valid_json_and_surfaces_vibe_stop_reason(
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
        stderr=(
            b"diagnostic from vibe\n"
            b"<vibe_stop_event>Turn limit of 50 reached</vibe_stop_event>\n"
        ),
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
        role="Backend", return_code=7, error="Turn limit of 50 reached"
    )
    assert status_updates == [
        ("Backend", "working", "Working on: Implement the API"),
        ("Backend", "blocked", "Turn limit of 50 reached"),
    ]
    command = invocation["command"]
    assert isinstance(command, tuple)
    assert command[:3] == (sys.executable, "-m", "vibe.cli.entrypoint")
    assert command[command.index("--prompt") + 1] == "Implement the API"
    assert command[command.index("--workdir") + 1] == str(workdir)
    options = invocation["options"]
    assert isinstance(options, dict)
    expected_environment = {
        **environment,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "VIBE_ACTIVE_MODEL": "mistral-medium-3.5",
        "VIBE_WORKFLOW_WORKER": "1",
    }
    expected_environment["VIBE_MCP_SERVERS"] = json.dumps([
        {
            "name": "workflow",
            "transport": "stdio",
            "command": [sys.executable],
            "args": ["-m", "workflow_memory.server"],
            "env": {"WORKFLOW_DB": str(database_path)},
        }
    ])
    assert options["env"] == expected_environment

    json_lines = (
        (workdir / "logs" / "backend.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line) for line in json_lines] == [
        {"role": "assistant", "content": "✓"},
        {"role": "tool", "content": "done"},
    ]
    assert (workdir / "logs" / "backend.stderr.log").read_text(encoding="utf-8") == (
        "diagnostic from vibe\n"
        "<vibe_stop_event>Turn limit of 50 reached</vibe_stop_event>\n"
    )


@pytest.mark.asyncio
async def test_run_worker_accepts_completed_blackboard_status_after_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    process = FakeProcess(
        return_code=1,
        stdout=b"",
        stderr=b"<vibe_stop_event>Turn limit of 50 reached</vibe_stop_event>\n",
    )

    async def fake_create_subprocess_exec(
        *_command: str, **_options: object
    ) -> FakeProcess:
        return process

    async def fake_reported_done(_database_path: Path, role: str) -> bool:
        return role == "Planner"

    monkeypatch.setattr(
        orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(orchestrator, "_reported_done", fake_reported_done)
    monkeypatch.setattr(orchestrator, "build_worker_prompt", lambda role, goal: goal)

    result = await orchestrator.run_worker(
        "Planner",
        "Plan the game",
        workdir,
        workdir / "workflow.db",
        asyncio.get_running_loop().time() + 30,
        environment={"WORKFLOW_TEST": "present"},
    )

    assert result == orchestrator.WorkerResult(
        role="Planner", return_code=1, completed=True
    )
    assert result.succeeded is True


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
async def test_run_workflow_blocks_implementation_after_planner_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    lifecycle: list[str] = []

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
        lifecycle.append("finish:Planner")
        return orchestrator.WorkerResult(
            role=role, return_code=3, error="planner failed"
        )

    initialized: list[Path] = []
    monkeypatch.setattr(
        orchestrator,
        "reset_workflow_state",
        lambda path: initialized.append(Path(path)),
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
    assert initialized == [resolved_workdir / "workflow.db"]
    assert not (resolved_workdir / ".vibe").exists()
    assert lifecycle == ["start:Planner", "finish:Planner"]
    assert {result.role for result in results} == {"Planner", "Backend", "QA"}
    assert results[0].succeeded is False
    assert (
        results[1].error == "Planner did not complete; implementation was not started"
    )
    assert (
        results[2].error == "Planner did not complete; implementation was not started"
    )


@pytest.mark.asyncio
async def test_run_workflow_runs_planner_backend_and_qa_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    lifecycle: list[str] = []

    async def fake_run_worker(
        role: str,
        _goal: str,
        _workdir: Path,
        _database_path: Path,
        _deadline: float,
        *,
        environment: dict[str, str] | None = None,
    ) -> orchestrator.WorkerResult:
        assert environment == {"WORKFLOW_TEST": "present"}
        lifecycle.append(f"start:{role}")
        lifecycle.append(f"finish:{role}")
        if role == "Planner":
            return orchestrator.WorkerResult(role=role, return_code=1, completed=True)
        return orchestrator.WorkerResult(role=role, return_code=0)

    monkeypatch.setattr(orchestrator, "reset_workflow_state", lambda _path: None)
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
        "Build a Todo API", workdir, environment={"WORKFLOW_TEST": "present"}
    )

    assert lifecycle == [
        "start:Planner",
        "finish:Planner",
        "start:Backend",
        "finish:Backend",
        "start:QA",
        "finish:QA",
    ]
    assert all(result.succeeded for result in results)


@pytest.mark.asyncio
async def test_run_workflow_does_not_start_qa_after_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    started_roles: list[str] = []

    async def fake_run_worker(
        role: str,
        _goal: str,
        _workdir: Path,
        _database_path: Path,
        _deadline: float,
        *,
        environment: dict[str, str] | None = None,
    ) -> orchestrator.WorkerResult:
        assert environment == {"WORKFLOW_TEST": "present"}
        started_roles.append(role)
        if role == "Backend":
            return orchestrator.WorkerResult(
                role=role, return_code=1, error="backend failed"
            )
        return orchestrator.WorkerResult(role=role, return_code=0)

    monkeypatch.setattr(orchestrator, "reset_workflow_state", lambda _path: None)
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
        "Build a Todo API", workdir, environment={"WORKFLOW_TEST": "present"}
    )

    assert started_roles == ["Planner", "Backend"]
    assert results[2] == orchestrator.WorkerResult(
        role="QA", return_code=-1, error="Backend did not complete; QA was not started"
    )
