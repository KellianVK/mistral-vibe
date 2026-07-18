from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from workflow_memory import store


def create_server(db_path: str | Path | None = None) -> FastMCP:
    database_path = _resolve_database_path(db_path)
    server = FastMCP("workflow-memory")

    @server.tool()
    def publish_decision(role: str, summary: str, artifact: str | None = None) -> str:
        """Publish a decision after every significant workflow step.

        Record plans, deliverables, interface contracts, and test findings as soon as
        they are established so the other agents can coordinate from shared facts.
        """
        return store.publish_decision(database_path, role, summary, artifact)

    @server.tool()
    def read_decisions(
        filter_role: str | None = None, since_id: int | None = None
    ) -> list[store.DecisionRecord]:
        """Read shared decisions before doing any work.

        Always call this tool first. Poll it again with since_id while waiting for
        another role, and use filter_role when only one agent's decisions matter.
        """
        return store.read_decisions(database_path, filter_role, since_id)

    @server.tool()
    def update_status(role: str, state: str, current_task: str) -> str:
        """Report this agent's current workflow state.

        Call this at the beginning of work and again whenever the task or state
        changes, especially when becoming blocked or finishing.
        """
        return store.update_status(database_path, role, state, current_task)

    return server


def _resolve_database_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser().resolve()
    if configured_path := os.environ.get("WORKFLOW_DB"):
        return Path(configured_path).expanduser().resolve()
    return store.workflow_database_path(Path.cwd())


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
