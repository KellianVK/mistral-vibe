"""In-session MiaouFlow controls: the tools behind the /workflow skill.

Available in any interactive session of this fork (hidden inside workflow
workers to prevent recursive teams). They drive the exact same orchestrator
as the `vibe workflow` subcommand, as a background task of the session.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.workflow import WORKFLOW_WORKER_ENV
from vibe.workflow.controller import ControllerState, WorkflowController, WorkflowStatus

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig


class WorkflowControlConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class WorkflowControlState(BaseToolState):
    pass


_controllers: dict[Path, WorkflowController] = {}


def _controller() -> WorkflowController:
    workdir = Path.cwd().resolve()
    if controller := _controllers.get(workdir):
        return controller
    controller = WorkflowController(workdir)
    _controllers[workdir] = controller
    return controller


def _not_inside_a_worker(config: AnyVibeConfig | None = None) -> bool:
    del config
    return os.environ.get(WORKFLOW_WORKER_ENV) != "1"


class WorkflowAgentResult(BaseModel):
    role: str
    state: str
    current_task: str


class WorkflowControlResult(BaseModel):
    ok: bool
    state: ControllerState
    message: str
    goal: str | None = None
    error: str | None = None
    board_url: str | None = None
    agents: list[WorkflowAgentResult] = Field(default_factory=list)


def _status_result(status: WorkflowStatus) -> WorkflowControlResult:
    return WorkflowControlResult(
        ok=status["state"] != "failed",
        state=status["state"],
        message="Workflow status loaded",
        goal=status["goal"],
        error=status["error"],
        board_url=status["board_url"],
        agents=[
            WorkflowAgentResult(
                role=agent["role"],
                state=agent["state"],
                current_task=agent["current_task"],
            )
            for agent in status["agents"]
        ],
    )


class StartWorkflowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, description="Software delivery goal for the team")


class StartWorkflow(
    BaseTool[
        StartWorkflowArgs,
        WorkflowControlResult,
        WorkflowControlConfig,
        WorkflowControlState,
    ]
):
    description = (
        "Start a MiaouFlow agent team on a goal, in the background of this "
        "session, with the live board. Same engine as `vibe workflow run`."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _not_inside_a_worker(config)

    async def run(
        self, args: StartWorkflowArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[WorkflowControlResult, None]:
        controller = _controller()
        action = await controller.start(args.goal)
        yield WorkflowControlResult(
            ok=action["ok"],
            state=action["state"],
            message=action["message"],
            goal=action["goal"],
            board_url=action["board_url"],
        )


class EmptyWorkflowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetWorkflowStatus(
    BaseTool[
        EmptyWorkflowArgs,
        WorkflowControlResult,
        WorkflowControlConfig,
        WorkflowControlState,
    ]
):
    description = "Read the background MiaouFlow run and per-agent status."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _not_inside_a_worker(config)

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
    description = "Cancel the background MiaouFlow run owned by this session."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _not_inside_a_worker(config)

    async def run(
        self, args: EmptyWorkflowArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[WorkflowControlResult, None]:
        controller = _controller()
        action = await controller.stop()
        yield WorkflowControlResult(
            ok=action["ok"],
            state=action["state"],
            message=action["message"],
            goal=action["goal"],
            board_url=action["board_url"],
        )
