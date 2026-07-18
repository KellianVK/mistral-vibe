from __future__ import annotations

from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

from rich.console import Console

import dashboard
from workflow_memory.store import initialize_database, publish_decision, update_status


def test_read_status_uses_read_only_connection_and_renders_decision_counts(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "workflow.db"
    initialize_database(database_path)
    update_status(database_path, "Backend", "done", "API implemented")
    publish_decision(database_path, "Backend", "Chose the route contract")
    publish_decision(database_path, "Backend", "Implemented both routes")
    update_status(database_path, "QA", "blocked", "Waiting for a fix")

    real_connect = dashboard.sqlite3.connect
    connections: list[tuple[object, dict[str, object]]] = []

    def connect(database, *args, **kwargs):
        connections.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(dashboard.sqlite3, "connect", connect)

    statuses = dashboard.read_status(database_path)

    assert statuses == [
        dashboard.AgentStatus(
            role="Backend",
            state="done",
            current_task="API implemented",
            decision_count=2,
        ),
        dashboard.AgentStatus(
            role="QA",
            state="blocked",
            current_task="Waiting for a fix",
            decision_count=0,
        ),
    ]
    assert connections == [
        (f"{database_path.resolve().as_uri()}?mode=ro", {"uri": True, "timeout": 5.0})
    ]

    output = StringIO()
    console = Console(file=output, color_system=None, width=500)
    console.print(dashboard.render_status(database_path))
    assert output.getvalue() == (
        f"vibe-workflow · {database_path}\n"
        "├── ✔ Backend · API implemented (2 decisions)\n"
        "└── ⏸ QA · Waiting for a fix (0 decisions)\n"
    )


def test_missing_database_snapshot_is_empty_without_creating_file(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing.db"

    assert dashboard.read_status(database_path) == []

    output = StringIO()
    console = Console(file=output, color_system=None, width=500)
    console.print(dashboard.render_status(database_path))
    assert output.getvalue() == (
        f"vibe-workflow · {database_path}\n└── No agent status yet\n"
    )
    assert not database_path.exists()


def test_console_reconfigures_output_without_disabling_legacy_windows(
    monkeypatch,
) -> None:
    buffer = BytesIO()
    output = TextIOWrapper(buffer, encoding="cp1252")
    real_console = dashboard.Console
    console_options = {}

    def console_factory(*args, **kwargs):
        console_options.update(kwargs)
        return real_console(*args, **kwargs)

    monkeypatch.setattr(dashboard.sys, "stdout", output)
    monkeypatch.setattr(dashboard, "Console", console_factory)

    console = dashboard._console()
    console.print("⏸ ✔")
    output.flush()

    assert output.encoding == "utf-8"
    assert "legacy_windows" not in console_options
    assert buffer.getvalue().decode("utf-8").splitlines() == ["⏸ ✔"]
