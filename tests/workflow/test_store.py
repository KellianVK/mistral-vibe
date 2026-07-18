from __future__ import annotations

from pathlib import Path

import pytest

from vibe.workflow import store


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.db"
    store.initialize_database(path)
    return path


def test_decisions_roundtrip_with_topic_filters(db: Path) -> None:
    store.publish_decision(db, "Planner", "the plan", topic="plan")
    store.publish_decision(db, "Backend", "login returns a JWT", topic="auth-contract")
    store.publish_decision(db, "Backend", "misc note")

    assert [d["role"] for d in store.read_decisions(db)] == [
        "Planner",
        "Backend",
        "Backend",
    ]
    assert [d["summary"] for d in store.read_decisions(db, topic="auth-contract")] == [
        "login returns a JWT"
    ]
    assert [d["id"] for d in store.read_decisions(db, filter_role="Backend")] == [2, 3]
    assert [d["id"] for d in store.read_decisions(db, since_id=2)] == [3]


def test_update_status_upserts_and_validates(db: Path) -> None:
    store.update_status(db, "Backend", "working", "implementing")
    store.update_status(db, "Backend", "done", "finished")
    snapshot = store.read_status_snapshot(db)
    assert len(snapshot) == 1
    assert snapshot[0]["state"] == "done"

    with pytest.raises(ValueError, match="Invalid state"):
        store.update_status(db, "Backend", "napping", "zzz")


def test_question_lifecycle_blocks_and_unblocks_the_asker(db: Path) -> None:
    store.update_status(db, "Frontend", "working", "building UI")
    question_id = store.ask_question(
        db, "Frontend", "Backend", "What does /auth/login return?"
    )

    status = store.read_status(db, "Frontend")
    assert status is not None and status["state"] == "blocked"
    open_questions = store.read_questions(db, open_only=True, to_role="Backend")
    assert [q["id"] for q in open_questions] == [question_id]

    store.answer_question(db, question_id, "A JWT in {token, expires_in}")

    status = store.read_status(db, "Frontend")
    assert status is not None and status["state"] == "working"
    assert store.read_questions(db, open_only=True) == []
    answered = store.read_questions(db)[0]
    assert answered["answer"] == "A JWT in {token, expires_in}"
    assert answered["resolved"] is True


def test_answer_question_unknown_id_raises(db: Path) -> None:
    with pytest.raises(ValueError, match="No question with id"):
        store.answer_question(db, 99, "answer")


def test_claim_conflicts_and_release(db: Path) -> None:
    ok, holder = store.claim_file(db, "Backend", "server/app.py")
    assert (ok, holder) == (True, "Backend")

    ok, holder = store.claim_file(db, "Frontend", "server/app.py")
    assert (ok, holder) == (False, "Backend")

    # Re-claiming your own file refreshes it rather than conflicting.
    ok, _ = store.claim_file(db, "Backend", "server/app.py")
    assert ok is True

    assert store.release_file(db, "Frontend", "server/app.py") is False
    assert store.release_file(db, "Backend", "server/app.py") is True
    assert store.read_claims(db) == []


def test_events_produce_timings(db: Path) -> None:
    store.record_event(db, "Backend", "spawned")
    store.record_event(db, "Backend", "first_action")
    store.record_event(db, "Backend", "turn")
    store.record_event(db, "Backend", "turn")
    store.record_event(db, "Backend", "exited")

    timings = store.compute_timings(db)
    backend = timings["Backend"]
    assert backend["turns"] == 2
    assert backend["spawned_at"] is not None
    assert backend["first_action_s"] is not None and backend["first_action_s"] >= 0
    assert backend["total_s"] is not None and backend["total_s"] >= 0
    assert backend["avg_turn_s"] is not None

    with pytest.raises(ValueError, match="Invalid event kind"):
        store.record_event(db, "Backend", "meow")


def test_start_run_resets_the_board_but_keeps_run_history(db: Path) -> None:
    first = store.start_run(db, "first goal")
    store.publish_decision(db, "Planner", "old plan")
    store.update_status(db, "Planner", "done", "old")
    store.ask_question(db, "Frontend", "Backend", "old question")
    store.claim_file(db, "Backend", "app.py")
    store.record_event(db, "Planner", "spawned")

    second = store.start_run(db, "second goal")

    assert second == first + 1
    assert store.read_decisions(db) == []
    assert store.read_status_snapshot(db) == []
    assert store.read_questions(db) == []
    assert store.read_claims(db) == []
    assert store.compute_timings(db) == {}
    run = store.current_run(db)
    assert run is not None and run["goal"] == "second goal"


def test_read_board_state_wire_shape(db: Path) -> None:
    store.start_run(db, "Todo API")
    store.update_status(db, "Backend", "working", "implementing")
    store.publish_decision(db, "Backend", "contract", topic="auth-contract")
    store.ask_question(db, "Frontend", "Backend", "shape?")
    store.claim_file(db, "Backend", "app.py")

    state = store.read_board_state(db)

    assert state["goal"] == "Todo API"
    assert state["run"] is not None and state["run"]["goal"] == "Todo API"
    assert state["agents"]["Backend"]["status"] == "working"
    assert state["decisions"][0]["topic"] == "auth-contract"
    assert state["questions"][0]["from"] == "Frontend"
    assert state["questions"][0]["to"] == "Backend"
    assert state["claims"][0]["path"] == "app.py"
    assert state["timings"] == {}
