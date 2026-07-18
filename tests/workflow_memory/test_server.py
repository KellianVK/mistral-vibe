from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult
import pytest

from workflow_memory.server import create_server
from workflow_memory.store import (
    initialize_database,
    publish_decision,
    read_decisions,
    read_status_snapshot,
    reset_workflow_state,
    update_status,
)


def _result_value(result: CallToolResult) -> Any:
    assert result.isError is False
    assert result.structuredContent is not None
    return result.structuredContent["result"]


@pytest.mark.asyncio
async def test_mcp_tools_persist_workflow_state_in_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "workflow.db"
    server = create_server(db_path)

    async with create_connected_server_and_client_session(server) as client:
        listed_tools = await client.list_tools()
        assert {tool.name for tool in listed_tools.tools} == {
            "publish_decision",
            "read_decisions",
            "update_status",
        }

        status_result = await client.call_tool(
            "update_status",
            {"role": "Planner", "state": "working", "current_task": "plan"},
        )
        assert _result_value(status_result) == "Planner: working"

        first_result = await client.call_tool(
            "publish_decision",
            {
                "role": "Planner",
                "summary": "Use a JSON HTTP contract",
                "artifact": "docs/plan.md",
            },
        )
        first_id = int(_result_value(first_result))

        second_result = await client.call_tool(
            "publish_decision",
            {
                "role": "Backend",
                "summary": "Implemented the API contract",
                "artifact": None,
            },
        )
        second_id = int(_result_value(second_result))

        filtered_result = await client.call_tool(
            "read_decisions", {"filter_role": "Backend", "since_id": first_id}
        )
        filtered = _result_value(filtered_result)
        assert len(filtered) == 1
        assert filtered == [
            {
                "id": second_id,
                "ts": filtered[0]["ts"],
                "role": "Backend",
                "summary": "Implemented the API contract",
                "artifact": None,
            }
        ]

        invalid_status = await client.call_tool(
            "update_status",
            {"role": "Planner", "state": "paused", "current_task": "plan"},
        )
        assert invalid_status.isError is True

    async with create_connected_server_and_client_session(
        create_server(db_path)
    ) as client:
        persisted_result = await client.call_tool("read_decisions", {})
        persisted = _result_value(persisted_result)
        assert [decision["id"] for decision in persisted] == [first_id, second_id]

    snapshots = read_status_snapshot(db_path)
    assert len(snapshots) == 1
    assert snapshots[0]["role"] == "Planner"
    assert snapshots[0]["state"] == "working"
    assert snapshots[0]["current_task"] == "plan"
    assert snapshots[0]["decision_count"] == 1

    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute(
            "SELECT role, summary, artifact FROM decisions ORDER BY id"
        ).fetchall() == [
            ("Planner", "Use a JSON HTTP contract", "docs/plan.md"),
            ("Backend", "Implemented the API contract", None),
        ]
        assert connection.execute(
            "SELECT role, state, current_task FROM status"
        ).fetchone() == ("Planner", "working", "plan")


def test_concurrent_publishers_use_independent_sqlite_connections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workflow.db"
    initialize_database(db_path)

    def publish(index: int) -> int:
        return int(
            publish_decision(
                db_path, role=f"Worker-{index % 4}", summary=f"Decision {index}"
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        decision_ids = list(executor.map(publish, range(32)))

    assert len(set(decision_ids)) == 32
    assert sorted(decision_ids) == list(range(1, 33))
    decisions = read_decisions(db_path)
    assert len(decisions) == 32
    assert {decision["summary"] for decision in decisions} == {
        f"Decision {index}" for index in range(32)
    }


def test_reset_workflow_state_starts_a_fresh_run(tmp_path: Path) -> None:
    db_path = tmp_path / "workflow.db"
    publish_decision(db_path, "Planner", "Old plan")
    update_status(db_path, "Planner", "done", "Old run completed")

    reset_workflow_state(db_path)

    assert read_decisions(db_path) == []
    assert read_status_snapshot(db_path) == []
    assert publish_decision(db_path, "Planner", "New plan") == "1"
