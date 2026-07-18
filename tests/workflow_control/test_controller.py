from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from orchestrator import WorkerResult
from workflow_control._controller import WorkflowController
from workflow_control.tools import GetWorkflowStatus, StartWorkflow, StopWorkflow


@pytest.mark.asyncio
async def test_controller_runs_workflow_in_background_and_reports_status(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    invocation: dict[str, object] = {}

    async def runner(
        goal: str, workdir: Path, environment: Mapping[str, str]
    ) -> list[WorkerResult]:
        invocation.update(goal=goal, workdir=workdir, environment=environment)
        started.set()
        await release.wait()
        return [
            WorkerResult(role="Planner", return_code=0),
            WorkerResult(role="Backend", return_code=0),
            WorkerResult(role="QA", return_code=0),
        ]

    controller = WorkflowController(
        tmp_path, runner=runner, environment={"MISTRAL_API_KEY": "test-key"}
    )

    assert await controller.start("Build a Todo API") == {
        "ok": True,
        "state": "running",
        "message": "Workflow started in the background",
        "goal": "Build a Todo API",
    }
    await asyncio.wait_for(started.wait(), timeout=1)
    assert invocation == {
        "goal": "Build a Todo API",
        "workdir": tmp_path.resolve(),
        "environment": {"MISTRAL_API_KEY": "test-key"},
    }
    assert (await controller.start("Start another workflow"))["ok"] is False

    release.set()
    status = await controller.status()
    for _ in range(20):
        if status["state"] == "complete":
            break
        await asyncio.sleep(0)
        status = await controller.status()
    assert status == {
        "state": "complete",
        "goal": "Build a Todo API",
        "error": None,
        "agents": [],
    }


@pytest.mark.asyncio
async def test_controller_stops_active_workflow(tmp_path: Path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    waiting = asyncio.Event()

    async def runner(
        _goal: str, _workdir: Path, _environment: Mapping[str, str]
    ) -> list[WorkerResult]:
        started.set()
        try:
            await waiting.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return []

    controller = WorkflowController(
        tmp_path, runner=runner, environment={"MISTRAL_API_KEY": "test-key"}
    )
    await controller.start("Build an API")
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await controller.stop() == {
        "ok": True,
        "state": "cancelled",
        "message": "Workflow stopped",
        "goal": "Build an API",
    }
    await asyncio.wait_for(cancelled.wait(), timeout=1)


def test_control_tools_are_hidden_inside_orchestrated_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_WORKFLOW_WORKER", "1")

    assert StartWorkflow.is_available() is False
    assert GetWorkflowStatus.is_available() is False
    assert StopWorkflow.is_available() is False
