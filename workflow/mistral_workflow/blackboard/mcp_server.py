import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mistral_workflow.blackboard.store import Blackboard, blackboard_path

app = FastMCP(name="blackboard")
_blackboard: Blackboard | None = None


def _get_blackboard() -> Blackboard:
    global _blackboard
    if _blackboard is None:
        project_dir = Path(os.environ.get("MISTRAL_WORKFLOW_PROJECT_DIR", ".")).resolve()
        _blackboard = Blackboard(blackboard_path(project_dir))
    return _blackboard


@app.tool()
def publish_decision(role: str, summary: str, artifact: str | None = None) -> str:
    """Publish a decision to the team's shared Blackboard, visible to every other role."""
    _get_blackboard().publish_decision(role, summary, artifact)
    return f"Published decision for '{role}'."


@app.tool()
def read_decisions(role: str | None = None) -> list[dict]:
    """Read decisions the team has logged so far, optionally filtered to one role."""
    return _get_blackboard().read_decisions(role)


@app.tool()
def request_review(role: str, target_role: str, question: str) -> str:
    """Ask another role a question via the Blackboard's shared question log."""
    _get_blackboard().request_review(role, target_role, question)
    return f"Question for '{target_role}' recorded on the Blackboard."


@app.tool()
def update_status(role: str, state: str, current_task: str | None = None) -> str:
    """Update this role's status on the Blackboard (idle/working/done/blocked/error)."""
    _get_blackboard().update_status(role, state, current_task)
    return f"Status for '{role}' updated to '{state}'."


def main() -> None:
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
