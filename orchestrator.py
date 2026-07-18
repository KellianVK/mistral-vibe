from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import sys

from dashboard import resolve_database_path, watch_status
from setup_workflow import (
    WorkflowConfigurationError,
    configure_workdir,
    workflow_database_path,
)
from workflow_memory.store import initialize_database, read_decisions, update_status

MAX_WORKFLOW_SECONDS = 10 * 60
MAX_PRICE = "1.00"
MAX_TURNS = "25"
STREAM_LIMIT_BYTES = 10 * 1024 * 1024
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class WorkerResult:
    role: str
    return_code: int
    timed_out: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0 and not self.timed_out and self.error is None


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


def build_worker_command(prompt: str, workdir: Path) -> list[str]:
    return [
        "vibe",
        "--prompt",
        prompt,
        "--output",
        "streaming",
        "--max-price",
        MAX_PRICE,
        "--max-turns",
        MAX_TURNS,
        "--agent",
        "auto-approve",
        "--auto-approve",
        "--workdir",
        str(workdir),
    ]


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as output:
        output.write(text)


def _truncate_log(path: Path) -> None:
    path.write_text("", encoding="utf-8")


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
    return build_worker_command(prompt, workdir), json_log, stderr_log


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
        return await _blocked_worker(
            database_path,
            role,
            f"Vibe exited with code {return_code}",
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
    child_environment = dict(environment if environment is not None else os.environ)
    child_environment.setdefault("PYTHONIOENCODING", "utf-8")
    child_environment.setdefault("PYTHONUTF8", "1")

    try:
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
    if not child_environment.get("MISTRAL_API_KEY"):
        raise WorkflowRunError("MISTRAL_API_KEY must be set in the environment")

    try:
        configure_workdir(resolved_workdir)
    except WorkflowConfigurationError as error:
        raise WorkflowRunError(str(error)) from error

    database_path = workflow_database_path(resolved_workdir)
    try:
        await asyncio.to_thread(initialize_database, database_path)
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise WorkflowRunError(
            f"Cannot initialize workflow database: {error}"
        ) from error
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
    parallel_since_id = await _latest_decision_id(database_path)
    backend_task = asyncio.create_task(
        run_worker(
            "Backend",
            goal,
            resolved_workdir,
            database_path,
            deadline,
            environment=child_environment,
        )
    )
    qa_task = asyncio.create_task(
        run_worker(
            "QA",
            goal,
            resolved_workdir,
            database_path,
            deadline,
            environment=child_environment,
        )
    )
    backend, qa = await asyncio.gather(backend_task, qa_task)
    backend = await _verify_published_decision(
        backend, database_path, parallel_since_id
    )
    qa = await _verify_published_decision(qa, database_path, parallel_since_id)
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
