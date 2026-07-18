from __future__ import annotations

from collections.abc import AsyncGenerator
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from workflow_control._controller import (
    WORKER_ENV,
    ControllerState,
    WorkflowAction,
    WorkflowController,
    WorkflowStatus,
)


class WorkflowControlConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class WorkflowControlState(BaseToolState):
    pass


class StartWorkflowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, description="Software delivery goal")


class EmptyWorkflowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    state: str
    current_task: str
    updated_at: str
    decision_count: int


class WorkflowControlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    state: ControllerState
    message: str
    goal: str | None = None
    error: str | None = None
    agents: list[WorkflowAgentResult] = Field(default_factory=list)


_controllers: dict[Path, WorkflowController] = {}


def _controller() -> WorkflowController:
    workdir = Path.cwd().resolve()
    if controller := _controllers.get(workdir):
        return controller
    controller = WorkflowController(workdir)
    _controllers[workdir] = controller
    return controller


def _action_result(action: WorkflowAction) -> WorkflowControlResult:
    return WorkflowControlResult(
        ok=action["ok"],
        state=action["state"],
        message=action["message"],
        goal=action["goal"],
    )


def _status_result(status: WorkflowStatus) -> WorkflowControlResult:
    return WorkflowControlResult(
        ok=status["state"] != "failed",
        state=status["state"],
        message="Workflow status loaded",
        goal=status["goal"],
        error=status["error"],
        agents=[
            WorkflowAgentResult.model_validate(agent) for agent in status["agents"]
        ],
    )


class StartWorkflow(
    BaseTool[
        StartWorkflowArgs,
        WorkflowControlResult,
        WorkflowControlConfig,
        WorkflowControlState,
    ]
):
    description = "Start the Planner, Backend, and QA workflow in the background."

    @classmethod
    def is_available(cls, config: object | None = None) -> bool:
        return os.environ.get(WORKER_ENV) != "1"

    async def run(
        self, args: StartWorkflowArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[WorkflowControlResult, None]:
        yield _action_result(await _controller().start(args.goal))


class GetWorkflowStatus(
    BaseTool[
        EmptyWorkflowArgs,
        WorkflowControlResult,
        WorkflowControlConfig,
        WorkflowControlState,
    ]
):
    description = "Read the background workflow and per-agent status."

    @classmethod
    def is_available(cls, config: object | None = None) -> bool:
        return os.environ.get(WORKER_ENV) != "1"

    async def run(
        self, args: EmptyWorkflowArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[WorkflowControlResult, None]:
        yield _status_result(await _controller().status())


class StopWorkflow(
    BaseTool[
        EmptyWorkflowArgs,
        WorkflowControlResult,
        WorkflowControlConfig,
        WorkflowControlState,
    ]
):
    description = "Cancel the background workflow owned by this Vibe session."

    @classmethod
    def is_available(cls, config: object | None = None) -> bool:
        return os.environ.get(WORKER_ENV) != "1"

    async def run(
        self, args: EmptyWorkflowArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[WorkflowControlResult, None]:
        yield _action_result(await _controller().stop())
