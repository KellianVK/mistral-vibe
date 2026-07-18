from __future__ import annotations

import pytest

from vibe.core.skills.builtins import BUILTIN_SKILLS
from vibe.core.tools.builtins import workflow_control
from vibe.workflow import WORKFLOW_WORKER_ENV


def test_workflow_skill_is_builtin_and_user_invocable() -> None:
    skill = BUILTIN_SKILLS["workflow"]
    assert skill.user_invocable is True
    assert "start_workflow" in skill.prompt
    assert "get_workflow_status" in skill.prompt
    assert "stop_workflow" in skill.prompt
    assert skill.allowed_tools == [
        "start_workflow",
        "get_workflow_status",
        "stop_workflow",
    ]


def test_vibe_self_awareness_skill_documents_miaouflow() -> None:
    vibe_skill = BUILTIN_SKILLS["vibe"]
    assert "MiaouFlow" in vibe_skill.prompt
    assert "/workflow" in vibe_skill.prompt
    assert "vibe workflow init" in vibe_skill.prompt


def test_control_tools_hidden_inside_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    for tool in (
        workflow_control.StartWorkflow,
        workflow_control.GetWorkflowStatus,
        workflow_control.StopWorkflow,
    ):
        monkeypatch.delenv(WORKFLOW_WORKER_ENV, raising=False)
        assert tool.is_available() is True
        monkeypatch.setenv(WORKFLOW_WORKER_ENV, "1")
        assert tool.is_available() is False


@pytest.mark.asyncio
async def test_start_status_stop_tools_roundtrip(tmp_path, monkeypatch) -> None:
    from vibe.core.tools.base import BaseToolState
    from vibe.workflow.controller import WorkflowController
    from vibe.workflow.orchestrator import WorkerResult

    async def runner(goal, workdir, environment):
        return [WorkerResult(role="Planner", return_code=0)]

    controller = WorkflowController(
        tmp_path, runner=runner, environment={}, board_port=None
    )
    monkeypatch.setattr(workflow_control, "_controller", lambda: controller)

    def make(tool_class):
        return tool_class(
            lambda: workflow_control.WorkflowControlConfig(), BaseToolState()
        )

    async def invoke(tool, args):
        results = [r async for r in tool.run(args)]
        assert len(results) == 1
        return results[0]

    start = await invoke(
        make(workflow_control.StartWorkflow),
        workflow_control.StartWorkflowArgs(goal="Ship a todo API"),
    )
    assert start.ok is True and start.state == "running"

    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)
    status = await invoke(
        make(workflow_control.GetWorkflowStatus), workflow_control.EmptyWorkflowArgs()
    )
    assert status.state == "complete"

    stop = await invoke(
        make(workflow_control.StopWorkflow), workflow_control.EmptyWorkflowArgs()
    )
    assert stop.ok is False  # nothing running anymore
