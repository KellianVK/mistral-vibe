"""Background controller so an interactive Vibe session can drive MiaouFlow.

Pattern ported from dev-kellian's workflow_control: the /workflow skill maps
onto three native tools (start_workflow / get_workflow_status /
stop_workflow) which delegate here. The controller runs the SAME
orchestrator as `vibe workflow run`, as an asyncio task on the interactive
session's event loop, and serves the live board on the side.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import StatusSnapshot, read_status_snapshot

if TYPE_CHECKING:
    from vibe.workflow.orchestrator import WorkerResult

DEFAULT_BOARD_PORT = 8787

type ControllerState = Literal[
    "idle", "running", "complete", "blocked", "failed", "cancelled"
]
type WorkflowRunner = Callable[
    [str, Path, Mapping[str, str]], Awaitable[list[WorkerResult]]
]


class WorkflowAction(TypedDict):
    ok: bool
    state: ControllerState
    message: str
    goal: str | None
    board_url: str | None


class WorkflowStatus(TypedDict):
    state: ControllerState
    goal: str | None
    error: str | None
    board_url: str | None
    agents: list[StatusSnapshot]


async def _run_configured_workflow(
    goal: str, workdir: Path, environment: Mapping[str, str]
) -> list[WorkerResult]:
    from vibe.workflow.orchestrator import run_workflow

    return await run_workflow(goal, workdir, environment=environment)


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


class WorkflowController:
    def __init__(
        self,
        workdir: Path,
        *,
        runner: WorkflowRunner = _run_configured_workflow,
        environment: Mapping[str, str] | None = None,
        board_port: int | None = DEFAULT_BOARD_PORT,
    ) -> None:
        self.workdir = workdir.expanduser().resolve()
        self._runner = runner
        self._environment = dict(environment if environment is not None else os.environ)
        self._board_port = board_port
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._board_task: asyncio.Task[None] | None = None
        self._state: ControllerState = "idle"
        self._goal: str | None = None
        self._error: str | None = None

    @property
    def board_url(self) -> str | None:
        if self._board_port is None:
            return None
        return f"http://127.0.0.1:{self._board_port}"

    async def start(self, goal: str) -> WorkflowAction:
        normalized_goal = goal.strip()
        async with self._lock:
            if not normalized_goal:
                return self._action(False, "A non-empty workflow goal is required")
            if self._task is not None and not self._task.done():
                return self._action(False, "A workflow is already running")

            self._goal = normalized_goal
            self._error = None
            self._state = "running"
            self._ensure_board()
            self._task = asyncio.create_task(self._run(normalized_goal))
            message = "MiaouFlow team started in the background"
            if self.board_url:
                message += f" — live board: {self.board_url}"
            return self._action(True, message)

    async def status(self) -> WorkflowStatus:
        agents = await asyncio.to_thread(
            read_status_snapshot, workflow_database_path(self.workdir)
        )
        async with self._lock:
            state = self._state
            if state == "idle":
                state = _infer_state(agents)
            return WorkflowStatus(
                state=state,
                goal=self._goal,
                error=self._error,
                board_url=self.board_url,
                agents=agents,
            )

    async def stop(self) -> WorkflowAction:
        async with self._lock:
            task = self._task
            if task is None or task.done():
                return self._action(False, "No workflow is currently running")
            task.cancel()

        with suppress(asyncio.CancelledError):
            await task
        async with self._lock:
            # A task cancelled before its first tick never runs _run's except
            # clause — normalize the state here.
            if task.cancelled() and self._state == "running":
                self._state = "cancelled"
                self._error = None
        return self._action(True, "Workflow stopped")

    def _ensure_board(self) -> None:
        if self._board_port is None:
            return
        if self._board_task is not None and not self._board_task.done():
            return
        if _port_in_use(self._board_port):
            return
        from vibe.workflow.server import serve_async

        self._board_task = asyncio.create_task(
            serve_async(self.workdir, self._board_port)
        )

    async def _run(self, goal: str) -> None:
        try:
            results = await self._runner(goal, self.workdir, self._environment)
        except asyncio.CancelledError:
            self._state = "cancelled"
            self._error = None
            raise
        except Exception as error:
            self._state = "failed"
            self._error = str(error)
            return

        blocked = [result for result in results if not result.succeeded]
        if not blocked:
            self._state = "complete"
            self._error = None
            return

        self._state = "blocked"
        self._error = "; ".join(
            f"{result.role}: {result.error or f'exit {result.return_code}'}"
            for result in blocked
        )

    def _action(self, ok: bool, message: str) -> WorkflowAction:
        return WorkflowAction(
            ok=ok,
            state=self._state,
            message=message,
            goal=self._goal,
            board_url=self.board_url,
        )


def _infer_state(agents: list[StatusSnapshot]) -> ControllerState:
    states = {agent["state"] for agent in agents}
    if "working" in states:
        return "running"
    if "blocked" in states:
        return "blocked"
    if states and states == {"done"}:
        return "complete"
    return "idle"
