from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from dashboard import resolve_database_path, watch_status
from vibe.core.utils.io import read_safe
from vibe.core.utils.tags import VIBE_STOP_EVENT_TAG
from workflow_memory.store import (
    read_decisions,
    read_status_snapshot,
    reset_workflow_state,
    update_status,
    workflow_database_path,
)

MAX_WORKFLOW_SECONDS = 30 * 60
MAX_PRICE = "5.00"
MAX_TURNS_BY_ROLE = {"Planner": "50", "Backend": "50", "QA": "50"}
WORKFLOW_MODEL = "mistral-medium-3.5"
STREAM_LIMIT_BYTES = 10 * 1024 * 1024
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MCP_SERVERS_ENV = "VIBE_MCP_SERVERS"
WORKER_ENV = "VIBE_WORKFLOW_WORKER"
STOP_EVENT_PATTERN = re.compile(
    rf"<{VIBE_STOP_EVENT_TAG}>(.*?)</{VIBE_STOP_EVENT_TAG}>", re.DOTALL
)
COORDINATION_TOOLS = [
    "workflow_read_decisions",
    "workflow_publish_decision",
    "workflow_update_status",
]
ENABLED_TOOLS_BY_ROLE = {
    "Planner": ["read_file", "grep", *COORDINATION_TOOLS],
    "Backend": ["bash", "read_file", "grep", "write_file", "edit", *COORDINATION_TOOLS],
    "QA": ["bash", "read_file", "grep", "write_file", "edit", *COORDINATION_TOOLS],
}


@dataclass(frozen=True)
class WorkerResult:
    role: str
    return_code: int
    timed_out: bool = False
    error: str | None = None
    completed: bool = False

    @property
    def succeeded(self) -> bool:
        exited_cleanly = self.return_code == 0 or self.completed
        return exited_cleanly and not self.timed_out and self.error is None


class WorkflowRunError(RuntimeError):
    pass


class WorkerLaunchError(RuntimeError):
    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass(frozen=True)
class RunningWorker:
    process: asyncio.subprocess.Process
    stdout: asyncio.StreamReader
    stderr: asyncio.StreamReader


def build_worker_prompt(role: str, goal: str) -> str:
    prompt_path = PROMPTS_DIR / f"{role.lower()}.md"
    if not prompt_path.is_file():
        raise WorkflowRunError(f"Missing role prompt: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").replace("{{GOAL}}", goal)


def build_worker_command(role: str, prompt: str, workdir: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "vibe.cli.entrypoint",
        "--prompt",
        prompt,
        "--output",
        "streaming",
        "--max-price",
        MAX_PRICE,
        "--max-turns",
        MAX_TURNS_BY_ROLE[role],
        "--agent",
        "auto-approve",
        "--auto-approve",
        "--workdir",
        str(workdir),
    ]
    for tool in ENABLED_TOOLS_BY_ROLE[role]:
        command.extend(["--enabled-tools", tool])
    return command


def build_worker_environment(
    environment: Mapping[str, str], database_path: Path
) -> dict[str, str]:
    child_environment = dict(environment)
    child_environment.pop("VIRTUAL_ENV", None)
    servers: list[dict[str, object]] = []
    if serialized_servers := child_environment.get(MCP_SERVERS_ENV):
        try:
            parsed_servers = json.loads(serialized_servers)
        except json.JSONDecodeError as error:
            raise WorkflowRunError(
                f"{MCP_SERVERS_ENV} must contain valid JSON"
            ) from error
        if not isinstance(parsed_servers, list) or not all(
            isinstance(server, dict) for server in parsed_servers
        ):
            raise WorkflowRunError(f"{MCP_SERVERS_ENV} must contain a JSON array")
        servers.extend(
            server for server in parsed_servers if server.get("name") != "workflow"
        )

    servers.append({
        "name": "workflow",
        "transport": "stdio",
        "command": [sys.executable],
        "args": ["-m", "workflow_memory.server"],
        "env": {"WORKFLOW_DB": str(database_path)},
    })
    child_environment[MCP_SERVERS_ENV] = json.dumps(servers)
    child_environment["VIBE_ACTIVE_MODEL"] = WORKFLOW_MODEL
    child_environment.setdefault("PYTHONIOENCODING", "utf-8")
    child_environment.setdefault("PYTHONUTF8", "1")
    child_environment[WORKER_ENV] = "1"
    return child_environment


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as output:
        output.write(text)


def _truncate_log(path: Path) -> None:
    path.write_text("", encoding="utf-8")


def _read_worker_stop_reason(path: Path) -> str | None:
    try:
        matches = STOP_EVENT_PATTERN.findall(read_safe(path).text)
    except OSError:
        return None
    if not matches:
        return None
    return " ".join(matches[-1].split())


async def _capture_json_stream(stream: asyncio.StreamReader, log_path: Path) -> None:
    while line := await stream.readline():
        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            continue
        try:
            event = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        serialized = f"{json.dumps(event, ensure_ascii=False)}\n"
        await asyncio.to_thread(_append_text, log_path, serialized)


async def _capture_text_stream(stream: asyncio.StreamReader, log_path: Path) -> None:
    while line := await stream.readline():
        decoded = line.decode("utf-8", errors="replace")
        await asyncio.to_thread(_append_text, log_path, decoded)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def _set_status(
    database_path: Path, role: str, state: str, current_task: str
) -> None:
    await asyncio.to_thread(update_status, database_path, role, state, current_task)


async def _reported_done(database_path: Path, role: str) -> bool:
    statuses = await asyncio.to_thread(read_status_snapshot, database_path)
    return any(
        status["role"] == role and status["state"] == "done" for status in statuses
    )


async def _blocked_worker(
    database_path: Path,
    role: str,
    message: str,
    *,
    return_code: int = -1,
    timed_out: bool = False,
) -> WorkerResult:
    await _set_status(database_path, role, "blocked", message)
    return WorkerResult(
        role=role,
        return_code=return_code,
        timed_out=timed_out,
        error=None if timed_out else message,
    )


async def _verify_published_decision(
    result: WorkerResult, database_path: Path, since_id: int | None
) -> WorkerResult:
    if not result.succeeded:
        return result
    decisions = await asyncio.to_thread(
        read_decisions, database_path, result.role, since_id
    )
    if decisions:
        return result
    return await _blocked_worker(
        database_path,
        result.role,
        f"{result.role} exited successfully without publishing a decision",
        return_code=result.return_code,
    )


async def _latest_decision_id(database_path: Path) -> int | None:
    decisions = await asyncio.to_thread(read_decisions, database_path, None, None)
    return decisions[-1]["id"] if decisions else None


async def _prepare_worker(
    role: str, goal: str, workdir: Path
) -> tuple[list[str], Path, Path]:
    logs_dir = workdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_log = logs_dir / f"{role.lower()}.jsonl"
    stderr_log = logs_dir / f"{role.lower()}.stderr.log"
    prompt = build_worker_prompt(role, goal)
    await asyncio.gather(
        asyncio.to_thread(_truncate_log, json_log),
        asyncio.to_thread(_truncate_log, stderr_log),
    )
    return build_worker_command(role, prompt, workdir), json_log, stderr_log


async def _launch_worker(
    command: Sequence[str], environment: Mapping[str, str], timeout: float
) -> RunningWorker:
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(environment),
                limit=STREAM_LIMIT_BYTES,
            )
    except TimeoutError as error:
        raise WorkerLaunchError("Global timeout reached", timed_out=True) from error
    except (FileNotFoundError, OSError) as error:
        raise WorkerLaunchError(f"Could not start Vibe: {error}") from error

    if process.stdout is None or process.stderr is None:
        await _stop_process(process)
        raise WorkerLaunchError("Vibe subprocess streams are unavailable")
    return RunningWorker(process, process.stdout, process.stderr)


async def _monitor_worker(
    running: RunningWorker,
    role: str,
    database_path: Path,
    deadline: float,
    json_log: Path,
    stderr_log: Path,
) -> WorkerResult:
    stdout_task = asyncio.create_task(_capture_json_stream(running.stdout, json_log))
    stderr_task = asyncio.create_task(_capture_text_stream(running.stderr, stderr_log))
    remaining = deadline - asyncio.get_running_loop().time()
    try:
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            return_code = await running.process.wait()
            await asyncio.gather(stdout_task, stderr_task)
    except TimeoutError:
        await _stop_process(running.process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return await _blocked_worker(
            database_path, role, "Global timeout reached", timed_out=True
        )
    except asyncio.CancelledError:
        await _stop_process(running.process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        await _set_status(database_path, role, "blocked", "Orchestrator cancelled")
        raise
    except Exception as error:
        await _stop_process(running.process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return await _blocked_worker(
            database_path, role, f"Worker execution failed: {error}"
        )

    if return_code != 0:
        if await _reported_done(database_path, role):
            return WorkerResult(role=role, return_code=return_code, completed=True)
        stop_reason = await asyncio.to_thread(_read_worker_stop_reason, stderr_log)
        return await _blocked_worker(
            database_path,
            role,
            stop_reason or f"Vibe exited with code {return_code}",
            return_code=return_code,
        )

    await _set_status(database_path, role, "done", "Completed")
    return WorkerResult(role=role, return_code=return_code)


async def run_worker(
    role: str,
    goal: str,
    workdir: Path,
    database_path: Path,
    deadline: float,
    *,
    environment: Mapping[str, str] | None = None,
) -> WorkerResult:
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        return await _blocked_worker(
            database_path, role, "Global timeout reached", timed_out=True
        )

    await _set_status(database_path, role, "working", f"Working on: {goal}")
    try:
        child_environment = build_worker_environment(
            environment if environment is not None else os.environ, database_path
        )
        command, json_log, stderr_log = await _prepare_worker(role, goal, workdir)
    except (OSError, WorkflowRunError) as error:
        return await _blocked_worker(database_path, role, str(error))

    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise WorkerLaunchError("Global timeout reached", timed_out=True)
        running = await _launch_worker(command, child_environment, remaining)
    except WorkerLaunchError as error:
        return await _blocked_worker(
            database_path, role, str(error), timed_out=error.timed_out
        )

    return await _monitor_worker(
        running, role, database_path, deadline, json_log, stderr_log
    )


async def run_workflow(
    goal: str,
    workdir: Path,
    *,
    timeout_seconds: float = MAX_WORKFLOW_SECONDS,
    environment: Mapping[str, str] | None = None,
) -> list[WorkerResult]:
    resolved_workdir = workdir.expanduser().resolve()
    child_environment = dict(environment if environment is not None else os.environ)

    database_path = workflow_database_path(resolved_workdir)
    try:
        await asyncio.to_thread(reset_workflow_state, database_path)
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise WorkflowRunError(f"Cannot prepare workflow database: {error}") from error
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    planner_since_id = await _latest_decision_id(database_path)

    planner = await run_worker(
        "Planner",
        goal,
        resolved_workdir,
        database_path,
        deadline,
        environment=child_environment,
    )
    planner = await _verify_published_decision(planner, database_path, planner_since_id)
    if not planner.succeeded:
        reason = "Planner did not complete; implementation was not started"
        backend, qa = await asyncio.gather(
            _blocked_worker(database_path, "Backend", reason),
            _blocked_worker(database_path, "QA", reason),
        )
        return [planner, backend, qa]

    backend_since_id = await _latest_decision_id(database_path)
    backend = await run_worker(
        "Backend",
        goal,
        resolved_workdir,
        database_path,
        deadline,
        environment=child_environment,
    )
    backend = await _verify_published_decision(backend, database_path, backend_since_id)
    if not backend.succeeded:
        reason = "Backend did not complete; QA was not started"
        qa = await _blocked_worker(database_path, "QA", reason)
        return [planner, backend, qa]

    qa_since_id = await _latest_decision_id(database_path)
    qa = await run_worker(
        "QA",
        goal,
        resolved_workdir,
        database_path,
        deadline,
        environment=child_environment,
    )
    qa = await _verify_published_decision(qa, database_path, qa_since_id)
    return [planner, backend, qa]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coordinate Mistral Vibe workers through a SQLite blackboard"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the demo workflow")
    run_parser.add_argument("--goal", required=True, help="Goal shared by all roles")
    run_parser.add_argument(
        "--workdir", type=Path, required=True, help="Trusted target project directory"
    )

    status_parser = subparsers.add_parser("status", help="Watch agent status")
    status_parser.add_argument("--workdir", type=Path, default=Path.cwd())
    status_parser.add_argument(
        "--once", action="store_true", help="Render one snapshot and exit"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        watch_status(resolve_database_path(args.workdir), once=args.once)
        return 0

    try:
        results = asyncio.run(run_workflow(args.goal, args.workdir))
    except WorkflowRunError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for result in results:
        outcome = "done" if result.succeeded else "blocked"
        print(f"{result.role}: {outcome}")
    return 0 if all(result.succeeded for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
