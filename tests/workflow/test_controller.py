from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibe.workflow.controller import WorkflowController
from vibe.workflow.orchestrator import WorkerResult
from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import initialize_database, update_status


def make_controller(tmp_path: Path, runner) -> WorkflowController:
    # board_port=None keeps tests network-free.
    return WorkflowController(tmp_path, runner=runner, environment={}, board_port=None)


@pytest.mark.asyncio
async def test_start_runs_in_background_and_completes(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def runner(goal, workdir, environment):
        assert goal == "Ship it" and workdir == tmp_path.resolve()
        started.set()
        return [WorkerResult(role="Planner", return_code=0)]

    controller = make_controller(tmp_path, runner)
    action = await controller.start("Ship it")
    assert action["ok"] is True and action["state"] == "running"
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0)
    status = await controller.status()
    assert status["state"] == "complete" and status["error"] is None


@pytest.mark.asyncio
async def test_only_one_run_at_a_time(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def runner(goal, workdir, environment):
        await release.wait()
        return []

    controller = make_controller(tmp_path, runner)
    assert (await controller.start("first"))["ok"] is True
    second = await controller.start("second")
    assert second["ok"] is False and "already running" in second["message"]
    release.set()


@pytest.mark.asyncio
async def test_stop_cancels_and_reports(tmp_path: Path) -> None:
    async def runner(goal, workdir, environment):
        await asyncio.sleep(30)
        return []

    controller = make_controller(tmp_path, runner)
    await controller.start("long run")
    action = await controller.stop()
    assert action["ok"] is True
    status = await controller.status()
    assert status["state"] == "cancelled"
    assert (await controller.stop())["ok"] is False


@pytest.mark.asyncio
async def test_blocked_results_surface_as_blocked(tmp_path: Path) -> None:
    async def runner(goal, workdir, environment):
        return [
            WorkerResult(role="Planner", return_code=0),
            WorkerResult(role="Backend", return_code=1, error="exit 1"),
        ]

    controller = make_controller(tmp_path, runner)
    await controller.start("goal")
    for _ in range(20):
        await asyncio.sleep(0)
    status = await controller.status()
    assert status["state"] == "blocked"
    assert status["error"] is not None and "Backend" in status["error"]


@pytest.mark.asyncio
async def test_status_reflects_blackboard_agents(tmp_path: Path) -> None:
    async def runner(goal, workdir, environment):
        return []

    controller = make_controller(tmp_path, runner)
    db = workflow_database_path(tmp_path)
    initialize_database(db)
    update_status(db, "Backend", "working", "implementing")
    status = await controller.status()
    assert [a["role"] for a in status["agents"]] == ["Backend"]
    assert status["state"] == "running"  # inferred from the blackboard
