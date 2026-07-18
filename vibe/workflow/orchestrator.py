"""MiaouFlow orchestrator: spawn headless Vibe workers wave by wave.

Each role runs as a separate ``python -m vibe`` subprocess (this fork, so the
native blackboard tools are present) against a shared workdir and SQLite
blackboard. Roles whose dependencies are satisfied run in parallel inside one
wave; the orchestrator stamps timing events while tailing each worker's
NDJSON stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys

from vibe.workflow import WORKFLOW_DB_ENV, WORKFLOW_ROLE_ENV, WORKFLOW_WORKER_ENV
from vibe.workflow.roles import RoleSpec, execution_waves, select_roles
from vibe.workflow.setup import (
    WorkflowConfigurationError,
    configure_workdir,
    workflow_database_path,
)
from vibe.workflow.store import (
    broadcast,
    initialize_database,
    read_claims,
    read_decisions,
    read_status,
    read_status_snapshot,
    record_change,
    record_event,
    start_run,
    update_status,
)

MAX_WORKFLOW_SECONDS = 10 * 60
MAX_PRICE = "2.00"
STREAM_LIMIT_BYTES = 10 * 1024 * 1024
BRIEF_FILE_MAP_LIMIT = 60
_SKIPPED_DIRS = {".git", ".vibe", "node_modules", "__pycache__", ".venv", "logs"}

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
STOP_EVENT_PATTERN = re.compile(r"<vibe_stop_event>(.*?)</vibe_stop_event>", re.DOTALL)


def _read_worker_stop_reason(stderr_log: Path) -> str | None:
    """Human-readable stop reason Vibe prints on stderr (turn/price limits)."""
    try:
        matches = STOP_EVENT_PATTERN.findall(
            stderr_log.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None
    if not matches:
        return None
    return " ".join(matches[-1].split())


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


def build_warm_start_brief(goal: str, roles: list[RoleSpec], workdir: Path) -> str:
    """Shared context injected into every prompt so agents skip re-discovery."""
    lines = [
        "## Shared team brief (identical for every agent — do not re-derive it)",
        f"Team goal: {goal}",
        "",
        "Team roles:",
    ]
    for role in roles:
        deps = ", ".join(role.depends_on) if role.depends_on else "none"
        lines.append(f"- {role.name}: {role.objective} (depends on: {deps})")

    entries = _file_map(workdir)
    lines += ["", f"Project files ({len(entries)} shown):"]
    lines += [f"- {entry}" for entry in entries] or ["- (empty project)"]
    return "\n".join(lines)


def _file_map(workdir: Path) -> list[str]:
    entries: list[str] = []
    for current_root, dirnames, filenames in os.walk(workdir):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIPPED_DIRS)
        relative_root = Path(current_root).relative_to(workdir)
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            entries.append(str(relative_root / filename))
            if len(entries) >= BRIEF_FILE_MAP_LIMIT:
                return entries
    return entries


DIFF_MAX_FILE_BYTES = 64 * 1024
DIFF_MAX_CHARS = 20_000


def _read_text_for_diff(full: Path, size: int) -> str | None:
    if size > DIFF_MAX_FILE_BYTES:
        return None
    try:
        return full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _snapshot_files(workdir: Path) -> dict[str, tuple[float, int, str | None]]:
    """Path -> (mtime, size, text content) used to diff what a worker touched.

    Content is captured only for small utf-8 files so diffs stay cheap.
    """
    snapshot: dict[str, tuple[float, int, str | None]] = {}
    for current_root, dirnames, filenames in os.walk(workdir):
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS]
        relative_root = Path(current_root).relative_to(workdir)
        for filename in filenames:
            if filename.startswith("."):
                continue
            full = Path(current_root) / filename
            try:
                stat = full.stat()
            except OSError:
                continue
            content = _read_text_for_diff(full, stat.st_size)
            snapshot[str(relative_root / filename)] = (
                stat.st_mtime,
                stat.st_size,
                content,
            )
    return snapshot


def _unified_diff(path_str: str, before: str | None, after: str | None) -> str | None:
    if before is None and after is None:
        return None
    diff_lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{path_str}",
        tofile=f"b/{path_str}",
    )
    diff = "".join(diff_lines)
    if not diff:
        return None
    if len(diff) > DIFF_MAX_CHARS:
        diff = diff[:DIFF_MAX_CHARS] + "\n… (diff truncated)\n"
    return diff


def _record_file_changes(
    database_path: Path,
    role: str,
    before: dict[str, tuple[float, int, str | None]],
    after: dict[str, tuple[float, int, str | None]],
) -> None:
    """Diff two snapshots into the changes feed, attributed via file claims.

    Waves run workers in parallel over one workdir, so a path claimed by a
    different role is skipped here — that role's own diff reports it.
    """
    claims = {claim["path"]: claim["role"] for claim in read_claims(database_path)}
    for path_str, (mtime, size, content) in after.items():
        holder = claims.get(path_str)
        if holder is not None and holder != role:
            continue
        if path_str not in before:
            record_change(
                database_path,
                role,
                path_str,
                "created",
                _unified_diff(path_str, None, content),
            )
        elif (before[path_str][0], before[path_str][1]) != (mtime, size):
            record_change(
                database_path,
                role,
                path_str,
                "modified",
                _unified_diff(path_str, before[path_str][2], content),
            )
    for path_str, (_, _, old_content) in before.items():
        if path_str not in after:
            holder = claims.get(path_str)
            if holder is None or holder == role:
                record_change(
                    database_path,
                    role,
                    path_str,
                    "deleted",
                    _unified_diff(path_str, old_content, None),
                )


def _decisions_digest(database_path: Path) -> str:
    decisions = read_decisions(database_path)
    if not decisions:
        return ""
    lines = ["", "Decisions already on the blackboard (read them, don't re-ask):"]
    for decision in decisions:
        topic = f" [{decision['topic']}]" if decision["topic"] else ""
        lines.append(f"- {decision['role']}{topic}: {decision['summary']}")
    return "\n".join(lines)


def build_worker_prompt(
    role: RoleSpec, goal: str, brief: str, database_path: Path
) -> str:
    prompt_path = PROMPTS_DIR / f"{role.agent_profile}.md"
    if not prompt_path.is_file():
        prompt_path = PROMPTS_DIR / "custom.md"
    if not prompt_path.is_file():
        raise WorkflowRunError(f"Missing role prompt: {prompt_path}")
    template = prompt_path.read_text(encoding="utf-8")
    full_brief = f"{brief}{_decisions_digest(database_path)}" if brief else ""
    return (
        template
        .replace("{{GOAL}}", goal)
        .replace("{{BRIEF}}", full_brief)
        .replace("{{ROLE}}", role.name)
        .replace("{{OBJECTIVE}}", role.objective)
    )


def build_worker_command(prompt: str, role: RoleSpec, workdir: Path) -> list[str]:
    return [
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
        role.agent_profile,
        "--max-price",
        MAX_PRICE,
        "--max-turns",
        str(role.max_turns),
        "--workdir",
        str(workdir),
    ]


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as output:
        output.write(text)


def _truncate_log(path: Path) -> None:
    path.write_text("", encoding="utf-8")


async def _capture_json_stream(
    stream: asyncio.StreamReader, log_path: Path, role: str, database_path: Path
) -> None:
    """Tail a worker's NDJSON stdout, logging it and stamping timing events."""
    first_action_recorded = False
    while line := await stream.readline():
        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            continue
        try:
            event = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("role") == "assistant":
            await asyncio.to_thread(record_event, database_path, role, "turn")
            if not first_action_recorded and event.get("tool_calls"):
                first_action_recorded = True
                await asyncio.to_thread(
                    record_event, database_path, role, "first_action"
                )
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
    decisions = await asyncio.to_thread(read_decisions, database_path)
    return decisions[-1]["id"] if decisions else None


async def _prepare_worker(
    role: RoleSpec, goal: str, brief: str, workdir: Path, database_path: Path
) -> tuple[list[str], Path, Path]:
    logs_dir = workdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_log = logs_dir / f"{role.agent_profile}.jsonl"
    stderr_log = logs_dir / f"{role.agent_profile}.stderr.log"
    prompt = await asyncio.to_thread(
        build_worker_prompt, role, goal, brief, database_path
    )
    await asyncio.gather(
        asyncio.to_thread(_truncate_log, json_log),
        asyncio.to_thread(_truncate_log, stderr_log),
    )
    return build_worker_command(prompt, role, workdir), json_log, stderr_log


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


async def _finalize_success(database_path: Path, role: str) -> None:
    """Mark a role done without clobbering a state it reported itself."""
    current = await asyncio.to_thread(read_status, database_path, role)
    if current is None or current["state"] == "working":
        await _set_status(database_path, role, "done", "Completed")


async def _monitor_worker(
    running: RunningWorker,
    role: str,
    database_path: Path,
    deadline: float,
    json_log: Path,
    stderr_log: Path,
) -> WorkerResult:
    stdout_task = asyncio.create_task(
        _capture_json_stream(running.stdout, json_log, role, database_path)
    )
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
    finally:
        await asyncio.to_thread(record_event, database_path, role, "exited")

    if return_code != 0:
        current = await asyncio.to_thread(read_status, database_path, role)
        if current is not None and current["state"] == "done":
            # The agent finished its work and said so; the process died in
            # teardown (e.g. a leaked child holding the event loop). The
            # decision check in _verify_published_decision still applies.
            print(
                f"warning: {role} exited with code {return_code} after "
                "reporting done; keeping done",
                file=sys.stderr,
            )
            return WorkerResult(role=role, return_code=0)
        stop_reason = await asyncio.to_thread(_read_worker_stop_reason, stderr_log)
        return await _blocked_worker(
            database_path,
            role,
            stop_reason or f"Vibe exited with code {return_code}",
            return_code=return_code,
        )

    await _finalize_success(database_path, role)
    return WorkerResult(role=role, return_code=return_code)


async def run_worker(
    role: RoleSpec,
    goal: str,
    brief: str,
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
            database_path, role.name, "Global timeout reached", timed_out=True
        )

    await _set_status(database_path, role.name, "working", f"Working on: {goal}")
    child_environment = dict(environment if environment is not None else os.environ)
    child_environment.setdefault("PYTHONIOENCODING", "utf-8")
    child_environment.setdefault("PYTHONUTF8", "1")
    child_environment[WORKFLOW_WORKER_ENV] = "1"
    child_environment[WORKFLOW_DB_ENV] = str(database_path)
    child_environment[WORKFLOW_ROLE_ENV] = role.name

    try:
        command, json_log, stderr_log = await _prepare_worker(
            role, goal, brief, workdir, database_path
        )
    except (OSError, WorkflowRunError) as error:
        return await _blocked_worker(database_path, role.name, str(error))

    files_before = await asyncio.to_thread(_snapshot_files, workdir)
    await asyncio.to_thread(record_event, database_path, role.name, "spawned")
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise WorkerLaunchError("Global timeout reached", timed_out=True)
        running = await _launch_worker(command, child_environment, remaining)
    except WorkerLaunchError as error:
        return await _blocked_worker(
            database_path, role.name, str(error), timed_out=error.timed_out
        )

    result = await _monitor_worker(
        running, role.name, database_path, deadline, json_log, stderr_log
    )
    files_after = await asyncio.to_thread(_snapshot_files, workdir)
    await asyncio.to_thread(
        _record_file_changes, database_path, role.name, files_before, files_after
    )
    return result


def _waiting_task(role: RoleSpec) -> str:
    if not role.depends_on:
        return "Waiting to start"
    return f"Waiting for {', '.join(role.depends_on)}"


async def _initialize_role_statuses(database_path: Path, roles: list[RoleSpec]) -> None:
    """Show the whole team on the board immediately, queued roles included."""
    await asyncio.gather(
        *(
            _set_status(database_path, role.name, "idle", _waiting_task(role))
            for role in roles
        )
    )


async def _block_unstarted_roles(database_path: Path, roles: list[RoleSpec]) -> None:
    statuses = await asyncio.to_thread(read_status_snapshot, database_path)
    idle_roles = {status["role"] for status in statuses if status["state"] == "idle"}
    await asyncio.gather(
        *(
            _set_status(
                database_path,
                role.name,
                "blocked",
                "Orchestrator cancelled before starting",
            )
            for role in roles
            if role.name in idle_roles
        )
    )


async def _latest_qa_verdict(database_path: Path) -> str | None:
    decisions = await asyncio.to_thread(
        read_decisions, database_path, "QA", None, "qa-verdict"
    )
    return decisions[-1]["summary"] if decisions else None


async def _run_wave(
    wave: list[RoleSpec],
    goal: str,
    brief: str,
    workdir: Path,
    database_path: Path,
    deadline: float,
    environment: Mapping[str, str],
) -> list[WorkerResult]:
    wave_since_id = await _latest_decision_id(database_path)
    wave_results = await asyncio.gather(
        *(
            run_worker(
                role,
                goal,
                brief,
                workdir,
                database_path,
                deadline,
                environment=environment,
            )
            for role in wave
        )
    )
    return [
        await _verify_published_decision(result, database_path, wave_since_id)
        for result in wave_results
    ]


async def _quality_loop(
    roles: list[RoleSpec],
    goal: str,
    brief: str,
    workdir: Path,
    database_path: Path,
    deadline: float,
    environment: Mapping[str, str],
    max_loop_iterations: int,
) -> list[WorkerResult]:
    """On a QA FAIL verdict, re-run the implementers then QA, bounded.

    The failure needs no special plumbing into the retry prompts: the FAIL
    decision is already on the blackboard, so the decisions digest injected
    at spawn carries it. The brief just adds emphasis.
    """
    qa_spec = next((role for role in roles if role.name == "QA"), None)
    if qa_spec is None or max_loop_iterations <= 0:
        return []

    results: list[WorkerResult] = []
    for iteration in range(1, max_loop_iterations + 1):
        verdict = await _latest_qa_verdict(database_path)
        if verdict is None or not verdict.strip().upper().startswith("FAIL"):
            return results

        implementers = [
            role
            for role in roles
            if role.name in qa_spec.depends_on and role.name != "Planner"
        ]
        if not implementers:
            return results
        await asyncio.to_thread(
            broadcast,
            database_path,
            "Orchestrator",
            f"QA failed — retry {iteration}/{max_loop_iterations}: re-running "
            f"{', '.join(role.name for role in implementers)} with the failure "
            "in context",
        )
        retry_brief = (
            f"{brief}\n\nPREVIOUS ATTEMPT FAILED QA. Fix exactly what the QA "
            f"verdict reports, do not start over: {verdict}"
        )
        results += await _run_wave(
            implementers,
            goal,
            retry_brief,
            workdir,
            database_path,
            deadline,
            environment,
        )
        results += await _run_wave(
            [qa_spec], goal, brief, workdir, database_path, deadline, environment
        )

    verdict = await _latest_qa_verdict(database_path)
    if verdict is not None and verdict.strip().upper().startswith("FAIL"):
        await asyncio.to_thread(
            broadcast,
            database_path,
            "Orchestrator",
            f"Quality loop ceiling reached ({max_loop_iterations} retries); "
            f"last QA verdict still failing: {verdict[:160]}",
        )
    return results


async def run_workflow(
    goal: str,
    workdir: Path,
    *,
    role_names: list[str] | None = None,
    timeout_seconds: float | None = None,
    warm_start: bool = True,
    max_loop_iterations: int = 3,
    environment: Mapping[str, str] | None = None,
) -> list[WorkerResult]:
    resolved_workdir = workdir.expanduser().resolve()
    child_environment = dict(environment if environment is not None else os.environ)

    if role_names is None:
        # A team composed by `vibe workflow init` applies to every entry
        # point, including /workflow started from an interactive session.
        from vibe.workflow.init_flow import load_team_config

        team_config = load_team_config(resolved_workdir)
        if team_config is not None:
            configured = team_config.get("roles")
            if isinstance(configured, list) and configured:
                role_names = [str(name) for name in configured]

    roles = select_roles(role_names, workdir=resolved_workdir)
    waves = execution_waves(roles)
    try:
        configure_workdir(resolved_workdir, roles)
    except WorkflowConfigurationError as error:
        raise WorkflowRunError(str(error)) from error

    database_path = workflow_database_path(resolved_workdir)
    try:
        await asyncio.to_thread(initialize_database, database_path)
        await asyncio.to_thread(start_run, database_path, goal)
        await _initialize_role_statuses(database_path, roles)
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise WorkflowRunError(
            f"Cannot initialize workflow database: {error}"
        ) from error

    brief = build_warm_start_brief(goal, roles, resolved_workdir) if warm_start else ""
    deadline = asyncio.get_running_loop().time() + (
        timeout_seconds if timeout_seconds is not None else MAX_WORKFLOW_SECONDS
    )

    results: list[WorkerResult] = []
    try:
        for wave in waves:
            results += await _run_wave(
                wave,
                goal,
                brief,
                resolved_workdir,
                database_path,
                deadline,
                child_environment,
            )
        results += await _quality_loop(
            roles,
            goal,
            brief,
            resolved_workdir,
            database_path,
            deadline,
            child_environment,
            max_loop_iterations,
        )
    except asyncio.CancelledError:
        await _block_unstarted_roles(database_path, roles)
        raise
    return results


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


async def _run_with_board(
    goal: str,
    workdir: Path,
    *,
    role_names: list[str] | None,
    timeout_seconds: float | None,
    warm_start: bool,
    board_port: int | None,
    open_browser: bool = False,
    max_loop_iterations: int = 3,
) -> list[WorkerResult]:
    server_task: asyncio.Task[None] | None = None
    if board_port is not None:
        if _port_in_use(board_port):
            print(f"Reusing MiaouFlow board already running on port {board_port}")
        else:
            from vibe.workflow.server import serve_async

            server_task = asyncio.create_task(serve_async(workdir, board_port))
            await asyncio.sleep(0.2)
        board_url = f"http://127.0.0.1:{board_port}"
        print(f"MiaouFlow board: {board_url}")
        if open_browser:
            import webbrowser

            await asyncio.to_thread(webbrowser.open, board_url)

    try:
        results = await run_workflow(
            goal,
            workdir,
            role_names=role_names,
            timeout_seconds=timeout_seconds,
            warm_start=warm_start,
            max_loop_iterations=max_loop_iterations,
        )
    except BaseException:
        if server_task is not None:
            server_task.cancel()
            with suppress(asyncio.CancelledError):
                await server_task
        raise

    for result in results:
        outcome = "done" if result.succeeded else "blocked"
        print(f"{result.role}: {outcome}")

    if server_task is not None:
        print(
            f"Run finished — board stays live at http://127.0.0.1:{board_port} "
            "(Ctrl-C to exit)"
        )
        with suppress(asyncio.CancelledError):
            await server_task
    return results


def run_workflow_command(
    goal: str,
    workdir: Path,
    *,
    role_names: list[str] | None = None,
    timeout_seconds: float | None = None,
    warm_start: bool = True,
    board_port: int | None = None,
    open_browser: bool = False,
    max_loop_iterations: int = 3,
) -> int:
    if role_names is None:
        from vibe.workflow.init_flow import load_team_config

        team_config = load_team_config(workdir)
        if team_config is not None:
            configured = team_config.get("roles")
            if isinstance(configured, list) and configured:
                print(f"Using the init team: {', '.join(str(n) for n in configured)}")

    try:
        results = asyncio.run(
            _run_with_board(
                goal,
                workdir,
                role_names=role_names,
                timeout_seconds=timeout_seconds,
                warm_start=warm_start,
                board_port=board_port,
                open_browser=open_browser,
                max_loop_iterations=max_loop_iterations,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except WorkflowRunError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0 if all(result.succeeded for result in results) else 1
