"""MiaouFlow blackboard tools.

Native coordination tools for `vibe workflow` agents. They only become
available when the orchestrator (or a user, deliberately) exports
``VIBE_WORKFLOW_DB``; every other Vibe session never sees them. The acting
role comes from ``VIBE_WORKFLOW_ROLE`` so an agent cannot impersonate a
teammate.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.workflow import WORKFLOW_DB_ENV, WORKFLOW_ROLE_ENV, store as blackboard_store

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig


class BlackboardConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BlackboardState(BaseToolState):
    pass


def _database_path() -> str:
    db_path = os.environ.get(WORKFLOW_DB_ENV)
    if not db_path:
        raise RuntimeError(f"{WORKFLOW_DB_ENV} is not set; no active workflow")
    return db_path


def _current_role() -> str:
    return os.environ.get(WORKFLOW_ROLE_ENV, "agent")


def _workflow_available(config: AnyVibeConfig | None = None) -> bool:
    del config
    return bool(os.environ.get(WORKFLOW_DB_ENV))


class DecisionModel(BaseModel):
    id: int
    ts: str
    role: str
    topic: str | None
    summary: str
    artifact: str | None


class QuestionModel(BaseModel):
    id: int
    ts: str
    from_role: str
    to_role: str
    question: str
    answer: str | None
    resolved: bool


class BlackboardAck(BaseModel):
    ok: bool
    message: str


class PublishDecisionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        description="One or two sentences a teammate can act on without asking you",
    )
    topic: str | None = Field(
        default=None,
        description=(
            "Short kebab-case key teammates can filter on, e.g. 'auth-contract'"
        ),
    )
    artifact: str | None = Field(
        default=None,
        description="Optional payload: an API contract, schema, file path, or snippet",
    )


class PublishDecision(
    BaseTool[PublishDecisionArgs, BlackboardAck, BlackboardConfig, BlackboardState]
):
    description = (
        "Publish a decision to the shared team blackboard so every other agent "
        "can read it. Publish interface contracts BEFORE implementing them."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: PublishDecisionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[BlackboardAck, None]:
        decision_id = await asyncio.to_thread(
            blackboard_store.publish_decision,
            _database_path(),
            _current_role(),
            args.summary,
            args.artifact,
            args.topic,
        )
        yield BlackboardAck(
            ok=True, message=f"Decision {decision_id} published to the blackboard"
        )


class ReadDecisionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter_role: str | None = Field(
        default=None, description="Only decisions published by this role"
    )
    topic: str | None = Field(
        default=None, description="Only decisions tagged with this topic"
    )
    since_id: int | None = Field(
        default=None,
        description="Only decisions with an id greater than this (incremental poll)",
    )


class ReadDecisionsResult(BaseModel):
    decisions: list[DecisionModel]
    count: int


class ReadDecisions(
    BaseTool[ReadDecisionsArgs, ReadDecisionsResult, BlackboardConfig, BlackboardState]
):
    description = (
        "Read decisions from the shared team blackboard. Call this before any "
        "work that touches an interface another role owns."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: ReadDecisionsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ReadDecisionsResult, None]:
        records = await asyncio.to_thread(
            blackboard_store.read_decisions,
            _database_path(),
            args.filter_role,
            args.since_id,
            args.topic,
        )
        decisions = [DecisionModel.model_validate(record) for record in records]
        yield ReadDecisionsResult(decisions=decisions, count=len(decisions))


class UpdateStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(description="One of: working, idle, blocked, done")
    current_task: str = Field(
        min_length=1, description="What you are doing right now, in a few words"
    )


class UpdateStatus(
    BaseTool[UpdateStatusArgs, BlackboardAck, BlackboardConfig, BlackboardState]
):
    description = (
        "Update your status on the shared team blackboard. Call this at every "
        "transition: when you start, block, unblock, and finish."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: UpdateStatusArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[BlackboardAck, None]:
        message = await asyncio.to_thread(
            blackboard_store.update_status,
            _database_path(),
            _current_role(),
            args.state,
            args.current_task,
        )
        yield BlackboardAck(ok=True, message=message)


class RequestReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_role: str = Field(
        min_length=1, description="The teammate role you need an answer from"
    )
    question: str = Field(
        min_length=1, description="The specific question blocking you"
    )


class RequestReviewResult(BaseModel):
    ok: bool
    question_id: int
    message: str


class RequestReview(
    BaseTool[RequestReviewArgs, RequestReviewResult, BlackboardConfig, BlackboardState]
):
    description = (
        "Ask another role a direct question via the blackboard. This marks you "
        "blocked on them — use it instead of inventing an answer, then poll "
        "read_decisions until they publish what you need."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: RequestReviewArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[RequestReviewResult, None]:
        question_id = await asyncio.to_thread(
            blackboard_store.ask_question,
            _database_path(),
            _current_role(),
            args.target_role,
            args.question,
        )
        yield RequestReviewResult(
            ok=True,
            question_id=question_id,
            message=(
                f"Question {question_id} sent to {args.target_role}; you are "
                "marked blocked until they answer or publish the decision"
            ),
        )


class AnswerQuestionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(description="Id of the open question to answer")
    answer: str = Field(min_length=1, description="Your answer to the teammate")


class AnswerQuestion(
    BaseTool[AnswerQuestionArgs, BlackboardAck, BlackboardConfig, BlackboardState]
):
    description = (
        "Answer an open question another role asked you on the blackboard; "
        "this unblocks them. Check open questions with read_questions."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: AnswerQuestionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[BlackboardAck, None]:
        message = await asyncio.to_thread(
            blackboard_store.answer_question,
            _database_path(),
            args.question_id,
            args.answer,
        )
        yield BlackboardAck(ok=True, message=message)


class ReadQuestionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_only: bool = Field(
        default=True, description="Only questions that have not been answered yet"
    )
    to_me: bool = Field(
        default=False, description="Only questions addressed to your role"
    )


class ReadQuestionsResult(BaseModel):
    questions: list[QuestionModel]
    count: int


class ReadQuestions(
    BaseTool[ReadQuestionsArgs, ReadQuestionsResult, BlackboardConfig, BlackboardState]
):
    description = (
        "List questions on the team blackboard. Check for open questions "
        "addressed to you before finishing your work."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: ReadQuestionsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ReadQuestionsResult, None]:
        records = await asyncio.to_thread(
            blackboard_store.read_questions,
            _database_path(),
            args.open_only,
            _current_role() if args.to_me else None,
        )
        questions = [QuestionModel.model_validate(record) for record in records]
        yield ReadQuestionsResult(questions=questions, count=len(questions))


class ClaimFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1, description="Repo-relative path of the file you will edit"
    )


class ClaimFile(
    BaseTool[ClaimFileArgs, BlackboardAck, BlackboardConfig, BlackboardState]
):
    description = (
        "Soft-lock a file on the blackboard before editing it. If a teammate "
        "already holds it, do NOT edit the file — coordinate via request_review."
    )

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: ClaimFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[BlackboardAck, None]:
        ok, holder = await asyncio.to_thread(
            blackboard_store.claim_file, _database_path(), _current_role(), args.path
        )
        if ok:
            yield BlackboardAck(ok=True, message=f"Claimed {args.path}")
        else:
            yield BlackboardAck(
                ok=False,
                message=(
                    f"CONFLICT: {args.path} is already claimed by {holder}. Do "
                    "not edit it; ask them via request_review instead."
                ),
            )


class ReleaseFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Path you previously claimed")


class ReleaseFile(
    BaseTool[ReleaseFileArgs, BlackboardAck, BlackboardConfig, BlackboardState]
):
    description = "Release a file you claimed so teammates can edit it."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _workflow_available(config)

    async def run(
        self, args: ReleaseFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[BlackboardAck, None]:
        released = await asyncio.to_thread(
            blackboard_store.release_file, _database_path(), _current_role(), args.path
        )
        message = (
            f"Released {args.path}"
            if released
            else f"You did not hold a claim on {args.path}"
        )
        yield BlackboardAck(ok=released, message=message)
