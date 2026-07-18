"""SQLite blackboard shared by every MiaouFlow agent.

One WAL-mode database file per workflow workdir. Agents (separate ``vibe -p``
processes) and the orchestrator all open short-lived connections against the
same file; SQLite WAL + busy timeouts provide the cross-process coordination.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Any, Literal, TypedDict

type DatabasePath = str | Path
type AgentState = Literal["working", "idle", "blocked", "done"]

VALID_STATES: frozenset[str] = frozenset({"working", "idle", "blocked", "done"})
EVENT_KINDS: frozenset[str] = frozenset({"spawned", "first_action", "turn", "exited"})


class DecisionRecord(TypedDict):
    id: int
    ts: str
    role: str
    topic: str | None
    summary: str
    artifact: str | None


class QuestionRecord(TypedDict):
    id: int
    ts: str
    from_role: str
    to_role: str
    question: str
    answer: str | None
    resolved: bool


class ClaimRecord(TypedDict):
    path: str
    role: str
    ts: str


class StatusSnapshot(TypedDict):
    role: str
    state: str
    current_task: str
    updated_at: str
    decision_count: int


class RunRecord(TypedDict):
    id: int
    goal: str
    started_at: str


class MessageRecord(TypedDict):
    id: int
    ts: str
    from_role: str
    to_role: str
    content: str
    read: bool


class BroadcastRecord(TypedDict):
    id: int
    ts: str
    from_role: str
    content: str


class ChangeRecord(TypedDict):
    id: int
    ts: str
    role: str
    path: str
    action: str
    diff: str | None


class RoleTiming(TypedDict):
    spawned_at: str | None
    first_action_s: float | None
    turns: int
    avg_turn_s: float | None
    total_s: float | None


_SCHEMAS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal TEXT NOT NULL,
        started_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        role TEXT NOT NULL,
        topic TEXT,
        summary TEXT NOT NULL,
        artifact TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS status (
        role TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        current_task TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        from_role TEXT NOT NULL,
        to_role TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT,
        resolved INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        path TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        ts TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        role TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        from_role TEXT NOT NULL,
        to_role TEXT NOT NULL,
        content TEXT NOT NULL,
        read INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        from_role TEXT NOT NULL,
        content TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        role TEXT NOT NULL,
        path TEXT NOT NULL,
        action TEXT NOT NULL,
        diff TEXT
    )
    """,
)

_initialized_databases: set[Path] = set()
_initialization_lock = Lock()


def initialize_database(db_path: DatabasePath) -> None:
    path = _normalize_path(db_path)
    if path in _initialized_databases:
        return

    with _initialization_lock:
        if path in _initialized_databases:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(_open_connection(path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise RuntimeError(f"Could not enable SQLite WAL mode for {path}")
            for schema in _SCHEMAS:
                connection.execute(schema)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(changes)").fetchall()
            }
            if columns and "diff" not in columns:
                connection.execute("ALTER TABLE changes ADD COLUMN diff TEXT")
            connection.commit()

        _initialized_databases.add(path)


def start_run(db_path: DatabasePath, goal: str) -> int:
    """Reset the board and open a fresh run so back-to-back demos start clean."""
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        wiped = (
            "decisions",
            "status",
            "questions",
            "claims",
            "events",
            "messages",
            "broadcasts",
            "changes",
        )
        for table in wiped:
            connection.execute(f"DELETE FROM {table}")
        cursor = connection.execute(
            "INSERT INTO runs (goal, started_at) VALUES (?, ?)", (goal, _utc_now())
        )
        run_id = cursor.lastrowid

    if run_id is None:
        raise RuntimeError("SQLite did not return an id for the new run")
    return run_id


def current_run(db_path: DatabasePath) -> RunRecord | None:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        row = connection.execute(
            "SELECT id, goal, started_at FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return RunRecord(id=row["id"], goal=row["goal"], started_at=row["started_at"])


def publish_decision(
    db_path: DatabasePath,
    role: str,
    summary: str,
    artifact: str | None = None,
    topic: str | None = None,
) -> str:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO decisions (ts, role, topic, summary, artifact)"
            " VALUES (?, ?, ?, ?, ?)",
            (_utc_now(), role, topic, summary, artifact),
        )
        decision_id = cursor.lastrowid

    if decision_id is None:
        raise RuntimeError("SQLite did not return an id for the published decision")
    return str(decision_id)


def read_decisions(
    db_path: DatabasePath,
    filter_role: str | None = None,
    since_id: int | None = None,
    topic: str | None = None,
) -> list[DecisionRecord]:
    path = _ready_database(db_path)
    conditions: list[str] = []
    parameters: list[str | int] = []

    if filter_role is not None:
        conditions.append("role = ?")
        parameters.append(filter_role)
    if since_id is not None:
        conditions.append("id > ?")
        parameters.append(since_id)
    if topic is not None:
        conditions.append("topic = ?")
        parameters.append(topic)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT id, ts, role, topic, summary, artifact FROM decisions"
        f"{where_clause} ORDER BY id ASC"
    )

    with closing(_open_connection(path)) as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        DecisionRecord(
            id=row["id"],
            ts=row["ts"],
            role=row["role"],
            topic=row["topic"],
            summary=row["summary"],
            artifact=row["artifact"],
        )
        for row in rows
    ]


def update_status(
    db_path: DatabasePath, role: str, state: str, current_task: str
) -> str:
    if state not in VALID_STATES:
        allowed = ", ".join(sorted(VALID_STATES))
        raise ValueError(f"Invalid state {state!r}; expected one of: {allowed}")

    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO status (role, state, current_task, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(role) DO UPDATE SET
                state = excluded.state,
                current_task = excluded.current_task,
                updated_at = excluded.updated_at
            """,
            (role, state, current_task, _utc_now()),
        )

    return f"{role}: {state}"


def read_status(db_path: DatabasePath, role: str) -> StatusSnapshot | None:
    for snapshot in read_status_snapshot(db_path):
        if snapshot["role"] == role:
            return snapshot
    return None


def read_status_snapshot(db_path: DatabasePath) -> list[StatusSnapshot]:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            """
            SELECT
                status.role,
                status.state,
                status.current_task,
                status.updated_at,
                COUNT(decisions.id) AS decision_count
            FROM status
            LEFT JOIN decisions ON decisions.role = status.role
            GROUP BY
                status.role,
                status.state,
                status.current_task,
                status.updated_at
            ORDER BY status.role ASC
            """
        ).fetchall()

    return [
        StatusSnapshot(
            role=row["role"],
            state=row["state"],
            current_task=row["current_task"],
            updated_at=row["updated_at"],
            decision_count=row["decision_count"],
        )
        for row in rows
    ]


def ask_question(
    db_path: DatabasePath, from_role: str, to_role: str, question: str
) -> int:
    """Record a cross-role question and mark the asker blocked on the target."""
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO questions (ts, from_role, to_role, question)"
            " VALUES (?, ?, ?, ?)",
            (_utc_now(), from_role, to_role, question),
        )
        question_id = cursor.lastrowid

    if question_id is None:
        raise RuntimeError("SQLite did not return an id for the question")
    update_status(db_path, from_role, "blocked", f"Waiting on {to_role}: {question}")
    return question_id


def answer_question(db_path: DatabasePath, question_id: int, answer: str) -> str:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        row = connection.execute(
            "SELECT from_role, resolved FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No question with id {question_id}")
        connection.execute(
            "UPDATE questions SET answer = ?, resolved = 1 WHERE id = ?",
            (answer, question_id),
        )

    asker = str(row["from_role"])
    if not row["resolved"]:
        current = read_status(db_path, asker)
        if current is not None and current["state"] == "blocked":
            update_status(db_path, asker, "working", "Unblocked by answer")
    return f"Question {question_id} answered; {asker} unblocked"


def read_questions(
    db_path: DatabasePath, open_only: bool = False, to_role: str | None = None
) -> list[QuestionRecord]:
    path = _ready_database(db_path)
    conditions: list[str] = []
    parameters: list[str] = []
    if open_only:
        conditions.append("resolved = 0")
    if to_role is not None:
        conditions.append("to_role = ?")
        parameters.append(to_role)
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT id, ts, from_role, to_role, question, answer, resolved FROM questions"
        f"{where_clause} ORDER BY id ASC"
    )

    with closing(_open_connection(path)) as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        QuestionRecord(
            id=row["id"],
            ts=row["ts"],
            from_role=row["from_role"],
            to_role=row["to_role"],
            question=row["question"],
            answer=row["answer"],
            resolved=bool(row["resolved"]),
        )
        for row in rows
    ]


def claim_file(
    db_path: DatabasePath, role: str, path_to_claim: str
) -> tuple[bool, str]:
    """Soft-lock a file. Returns (ok, holder); ok is False on conflict."""
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        row = connection.execute(
            "SELECT role FROM claims WHERE path = ?", (path_to_claim,)
        ).fetchone()
        if row is not None and row["role"] != role:
            return False, str(row["role"])
        connection.execute(
            "INSERT INTO claims (path, role, ts) VALUES (?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET role = excluded.role, ts = excluded.ts",
            (path_to_claim, role, _utc_now()),
        )
    return True, role


def release_file(db_path: DatabasePath, role: str, path_to_release: str) -> bool:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "DELETE FROM claims WHERE path = ? AND role = ?", (path_to_release, role)
        )
    return cursor.rowcount > 0


def read_claims(db_path: DatabasePath) -> list[ClaimRecord]:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            "SELECT path, role, ts FROM claims ORDER BY ts ASC"
        ).fetchall()
    return [
        ClaimRecord(path=row["path"], role=row["role"], ts=row["ts"]) for row in rows
    ]


def send_message(
    db_path: DatabasePath, from_role: str, to_role: str, content: str
) -> int:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO messages (ts, from_role, to_role, content)"
            " VALUES (?, ?, ?, ?)",
            (_utc_now(), from_role, to_role, content),
        )
        message_id = cursor.lastrowid
    if message_id is None:
        raise RuntimeError("SQLite did not return an id for the message")
    return message_id


def broadcast(db_path: DatabasePath, from_role: str, content: str) -> int:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO broadcasts (ts, from_role, content) VALUES (?, ?, ?)",
            (_utc_now(), from_role, content),
        )
        broadcast_id = cursor.lastrowid
    if broadcast_id is None:
        raise RuntimeError("SQLite did not return an id for the broadcast")
    return broadcast_id


def read_inbox(
    db_path: DatabasePath, role: str, mark_read: bool = True
) -> list[MessageRecord]:
    """Unread direct messages for a role, marked read on delivery."""
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        rows = connection.execute(
            "SELECT id, ts, from_role, to_role, content, read FROM messages"
            " WHERE to_role = ? AND read = 0 ORDER BY id ASC",
            (role,),
        ).fetchall()
        records = [
            MessageRecord(
                id=row["id"],
                ts=row["ts"],
                from_role=row["from_role"],
                to_role=row["to_role"],
                content=row["content"],
                read=bool(row["read"]),
            )
            for row in rows
        ]
        if mark_read and records:
            ids = [record["id"] for record in records]
            placeholders = ", ".join("?" for _ in ids)
            connection.execute(
                f"UPDATE messages SET read = 1 WHERE id IN ({placeholders})", ids
            )
    return records


def read_messages(db_path: DatabasePath) -> list[MessageRecord]:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            "SELECT id, ts, from_role, to_role, content, read FROM messages"
            " ORDER BY id ASC"
        ).fetchall()
    return [
        MessageRecord(
            id=row["id"],
            ts=row["ts"],
            from_role=row["from_role"],
            to_role=row["to_role"],
            content=row["content"],
            read=bool(row["read"]),
        )
        for row in rows
    ]


def read_broadcasts(db_path: DatabasePath) -> list[BroadcastRecord]:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            "SELECT id, ts, from_role, content FROM broadcasts ORDER BY id ASC"
        ).fetchall()
    return [
        BroadcastRecord(
            id=row["id"],
            ts=row["ts"],
            from_role=row["from_role"],
            content=row["content"],
        )
        for row in rows
    ]


def record_change(
    db_path: DatabasePath,
    role: str,
    path_str: str,
    action: str,
    diff: str | None = None,
) -> None:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        connection.execute(
            "INSERT INTO changes (ts, role, path, action, diff) VALUES (?, ?, ?, ?, ?)",
            (_utc_now(), role, path_str, action, diff),
        )


def read_changes(db_path: DatabasePath) -> list[ChangeRecord]:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            "SELECT id, ts, role, path, action, diff FROM changes ORDER BY id ASC"
        ).fetchall()
    return [
        ChangeRecord(
            id=row["id"],
            ts=row["ts"],
            role=row["role"],
            path=row["path"],
            action=row["action"],
            diff=row["diff"],
        )
        for row in rows
    ]


def read_change(db_path: DatabasePath, change_id: int) -> ChangeRecord | None:
    for change in read_changes(db_path):
        if change["id"] == change_id:
            return change
    return None


def record_event(
    db_path: DatabasePath, role: str, kind: str, payload: str | None = None
) -> None:
    if kind not in EVENT_KINDS:
        allowed = ", ".join(sorted(EVENT_KINDS))
        raise ValueError(f"Invalid event kind {kind!r}; expected one of: {allowed}")
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        connection.execute(
            "INSERT INTO events (ts, role, kind, payload) VALUES (?, ?, ?, ?)",
            (_utc_now(), role, kind, payload),
        )


def compute_timings(db_path: DatabasePath) -> dict[str, RoleTiming]:
    """Per-role latency metrics derived from orchestrator-stamped events."""
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection:
        rows = connection.execute(
            "SELECT ts, role, kind FROM events ORDER BY id ASC"
        ).fetchall()

    per_role: dict[str, dict[str, Any]] = {}
    for row in rows:
        role_events = per_role.setdefault(
            row["role"],
            {"spawned": None, "first_action": None, "turns": 0, "exited": None},
        )
        kind = row["kind"]
        if kind == "turn":
            role_events["turns"] += 1
        elif role_events.get(kind) is None:
            role_events[kind] = _parse_ts(row["ts"])

    timings: dict[str, RoleTiming] = {}
    for role, role_events in per_role.items():
        spawned: datetime | None = role_events["spawned"]
        first_action: datetime | None = role_events["first_action"]
        exited: datetime | None = role_events["exited"]
        turns: int = role_events["turns"]

        first_action_s = (
            (first_action - spawned).total_seconds()
            if spawned is not None and first_action is not None
            else None
        )
        total_s = (
            (exited - spawned).total_seconds()
            if spawned is not None and exited is not None
            else None
        )
        avg_turn_s = total_s / turns if total_s is not None and turns > 0 else None
        timings[role] = RoleTiming(
            spawned_at=spawned.isoformat() if spawned is not None else None,
            first_action_s=_round_or_none(first_action_s),
            turns=turns,
            avg_turn_s=_round_or_none(avg_turn_s),
            total_s=_round_or_none(total_s),
        )
    return timings


def read_board_state(db_path: DatabasePath) -> dict[str, Any]:
    """Aggregate snapshot in the wire shape the MiaouFlow board consumes."""
    run = current_run(db_path)
    agents: dict[str, dict[str, str]] = {}
    for snapshot in read_status_snapshot(db_path):
        agents[snapshot["role"]] = {
            "status": snapshot["state"],
            "current_task": snapshot["current_task"],
            "updated_at": snapshot["updated_at"],
        }
    decisions = [
        {
            "id": decision["id"],
            "ts": decision["ts"],
            "role": decision["role"],
            "topic": decision["topic"],
            "summary": decision["summary"],
            "artifact": decision["artifact"],
        }
        for decision in read_decisions(db_path)
    ]
    questions = [
        {
            "id": question["id"],
            "ts": question["ts"],
            "from": question["from_role"],
            "to": question["to_role"],
            "question": question["question"],
            "answer": question["answer"],
            "resolved": question["resolved"],
        }
        for question in read_questions(db_path)
    ]
    claims = [
        {"path": claim["path"], "role": claim["role"], "ts": claim["ts"]}
        for claim in read_claims(db_path)
    ]
    messages = [
        {
            "id": message["id"],
            "ts": message["ts"],
            "from": message["from_role"],
            "to": message["to_role"],
            "content": message["content"],
            "read": message["read"],
        }
        for message in read_messages(db_path)
    ]
    broadcasts_payload = [
        {
            "id": item["id"],
            "ts": item["ts"],
            "from": item["from_role"],
            "content": item["content"],
        }
        for item in read_broadcasts(db_path)
    ]
    changes = [
        {
            "id": change["id"],
            "ts": change["ts"],
            "role": change["role"],
            "path": change["path"],
            "action": change["action"],
            "has_diff": change["diff"] is not None,
        }
        for change in read_changes(db_path)
    ]
    return {
        "goal": run["goal"] if run is not None else None,
        "run": run,
        "agents": agents,
        "decisions": decisions,
        "questions": questions,
        "claims": claims,
        "messages": messages,
        "broadcasts": broadcasts_payload,
        "changes": changes,
        "timings": compute_timings(db_path),
    }


def _ready_database(db_path: DatabasePath) -> Path:
    path = _normalize_path(db_path)
    initialize_database(path)
    return path


def _normalize_path(db_path: DatabasePath) -> Path:
    return Path(db_path).expanduser().resolve()


def _open_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _round_or_none(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
