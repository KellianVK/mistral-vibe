from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Literal, TypedDict

type DatabasePath = str | Path
type AgentState = Literal["working", "idle", "blocked", "done"]

DEFAULT_DATABASE_NAME = "workflow.db"
VALID_STATES: frozenset[str] = frozenset({"working", "idle", "blocked", "done"})


class DecisionRecord(TypedDict):
    id: int
    ts: str
    role: str
    summary: str
    artifact: str | None


class StatusSnapshot(TypedDict):
    role: str
    state: str
    current_task: str
    updated_at: str
    decision_count: int


_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    summary TEXT NOT NULL,
    artifact TEXT
)
"""

_STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS status (
    role TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    current_task TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_initialized_databases: set[Path] = set()
_initialization_lock = Lock()


def workflow_database_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / DEFAULT_DATABASE_NAME


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
            connection.execute(_DECISIONS_SCHEMA)
            connection.execute(_STATUS_SCHEMA)
            connection.commit()

        _initialized_databases.add(path)


def publish_decision(
    db_path: DatabasePath, role: str, summary: str, artifact: str | None = None
) -> str:
    path = _ready_database(db_path)
    with closing(_open_connection(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO decisions (ts, role, summary, artifact) VALUES (?, ?, ?, ?)",
            (_utc_now(), role, summary, artifact),
        )
        decision_id = cursor.lastrowid

    if decision_id is None:
        raise RuntimeError("SQLite did not return an id for the published decision")
    return str(decision_id)


def read_decisions(
    db_path: DatabasePath, filter_role: str | None = None, since_id: int | None = None
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

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT id, ts, role, summary, artifact FROM decisions"
        f"{where_clause} ORDER BY id ASC"
    )

    with closing(_open_connection(path)) as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        DecisionRecord(
            id=row["id"],
            ts=row["ts"],
            role=row["role"],
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
