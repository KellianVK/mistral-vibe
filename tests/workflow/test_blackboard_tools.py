from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest

from vibe.core.tools.base import BaseTool, BaseToolState
from vibe.core.tools.builtins import blackboard
from vibe.workflow import WORKFLOW_DB_ENV, WORKFLOW_ROLE_ENV
from vibe.workflow.store import (
    initialize_database,
    read_claims,
    read_decisions,
    read_questions,
    read_status,
    update_status,
)

BLACKBOARD_TOOL_NAMES = {
    "publish_decision",
    "read_decisions",
    "update_status",
    "request_review",
    "answer_question",
    "read_questions",
    "claim_file",
    "release_file",
}

ResultT = TypeVar("ResultT")


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "workflow.db"
    initialize_database(path)
    monkeypatch.setenv(WORKFLOW_DB_ENV, str(path))
    monkeypatch.setenv(WORKFLOW_ROLE_ENV, "Backend")
    return path


async def invoke(tool: BaseTool, args: object) -> object:
    results = [result async for result in tool.run(args)]  # type: ignore[arg-type]
    assert len(results) == 1
    return results[0]


def make(tool_class: type) -> BaseTool:
    return tool_class(lambda: blackboard.BlackboardConfig(), BaseToolState())


def test_tools_hidden_without_workflow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKFLOW_DB_ENV, raising=False)
    for tool_class in (
        blackboard.PublishDecision,
        blackboard.ReadDecisions,
        blackboard.UpdateStatus,
        blackboard.RequestReview,
        blackboard.AnswerQuestion,
        blackboard.ReadQuestions,
        blackboard.ClaimFile,
        blackboard.ReleaseFile,
    ):
        assert tool_class.is_available() is False
        assert tool_class.get_name() in BLACKBOARD_TOOL_NAMES


def test_tools_available_with_workflow_env(db: Path) -> None:
    assert blackboard.PublishDecision.is_available() is True


@pytest.mark.asyncio
async def test_publish_and_read_decisions_use_role_from_env(db: Path) -> None:
    ack = await invoke(
        make(blackboard.PublishDecision),
        blackboard.PublishDecisionArgs(
            summary="login returns JWT", topic="auth-contract", artifact="POST /auth"
        ),
    )
    assert isinstance(ack, blackboard.BlackboardAck) and ack.ok

    stored = read_decisions(db)
    assert stored[0]["role"] == "Backend"
    assert stored[0]["topic"] == "auth-contract"

    result = await invoke(
        make(blackboard.ReadDecisions),
        blackboard.ReadDecisionsArgs(topic="auth-contract"),
    )
    assert isinstance(result, blackboard.ReadDecisionsResult)
    assert result.count == 1
    assert result.decisions[0].summary == "login returns JWT"


@pytest.mark.asyncio
async def test_update_status_and_claims(db: Path) -> None:
    ack = await invoke(
        make(blackboard.UpdateStatus),
        blackboard.UpdateStatusArgs(state="working", current_task="implementing"),
    )
    assert isinstance(ack, blackboard.BlackboardAck) and ack.ok
    status = read_status(db, "Backend")
    assert status is not None and status["state"] == "working"

    ack = await invoke(
        make(blackboard.ClaimFile), blackboard.ClaimFileArgs(path="server/app.py")
    )
    assert isinstance(ack, blackboard.BlackboardAck) and ack.ok
    assert read_claims(db)[0]["role"] == "Backend"

    ack = await invoke(
        make(blackboard.ReleaseFile), blackboard.ReleaseFileArgs(path="server/app.py")
    )
    assert isinstance(ack, blackboard.BlackboardAck) and ack.ok
    assert read_claims(db) == []


@pytest.mark.asyncio
async def test_claim_conflict_reports_holder(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await invoke(
        make(blackboard.ClaimFile), blackboard.ClaimFileArgs(path="server/app.py")
    )

    monkeypatch.setenv(WORKFLOW_ROLE_ENV, "Frontend")
    ack = await invoke(
        make(blackboard.ClaimFile), blackboard.ClaimFileArgs(path="server/app.py")
    )
    assert isinstance(ack, blackboard.BlackboardAck)
    assert ack.ok is False
    assert "Backend" in ack.message


@pytest.mark.asyncio
async def test_question_roundtrip_between_roles(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(WORKFLOW_ROLE_ENV, "Frontend")
    update_status(db, "Frontend", "working", "building")
    review = await invoke(
        make(blackboard.RequestReview),
        blackboard.RequestReviewArgs(
            target_role="Backend", question="What does /auth/login return?"
        ),
    )
    assert isinstance(review, blackboard.RequestReviewResult) and review.ok
    status = read_status(db, "Frontend")
    assert status is not None and status["state"] == "blocked"

    monkeypatch.setenv(WORKFLOW_ROLE_ENV, "Backend")
    questions = await invoke(
        make(blackboard.ReadQuestions),
        blackboard.ReadQuestionsArgs(open_only=True, to_me=True),
    )
    assert isinstance(questions, blackboard.ReadQuestionsResult)
    assert questions.count == 1

    ack = await invoke(
        make(blackboard.AnswerQuestion),
        blackboard.AnswerQuestionArgs(
            question_id=review.question_id, answer="{token, expires_in}"
        ),
    )
    assert isinstance(ack, blackboard.BlackboardAck) and ack.ok
    status = read_status(db, "Frontend")
    assert status is not None and status["state"] == "working"
    assert read_questions(db, open_only=True) == []
