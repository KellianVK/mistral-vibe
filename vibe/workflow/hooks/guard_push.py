"""pre_tool hook: block `git push` until the Reviewer publishes a GO verdict.

Wired into a workdir's ``.vibe/hooks.toml`` by MiaouFlow setup when the team
includes a Reviewer. Vibe sends the tool invocation as JSON on stdin; a JSON
``{"decision": "deny", "reason": ...}`` on stdout blocks the call. Anything
unexpected fails OPEN (plain exit 0) so a broken hook can never paralyze the
whole bash tool — except the explicit deny path, which is the point.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

REVIEW_TOPIC = "review-verdict"


def _deny(reason: str) -> None:
    print(json.dumps({"decision": "deny", "reason": reason}))


def _latest_review_verdict(workdir: Path) -> str | None:
    from vibe.workflow.setup import workflow_database_path
    from vibe.workflow.store import read_decisions

    database_path = workflow_database_path(workdir)
    if not database_path.exists():
        return None
    decisions = read_decisions(database_path, topic=REVIEW_TOPIC)
    return decisions[-1]["summary"] if decisions else None


def main() -> int:
    try:
        invocation = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if not isinstance(invocation, dict) or invocation.get("tool_name") != "bash":
        return 0
    tool_input = invocation.get("tool_input") or {}
    command = str(tool_input.get("command", ""))
    if "git push" not in command:
        return 0

    try:
        verdict = _latest_review_verdict(Path(invocation.get("cwd") or "."))
    except Exception:
        return 0

    if verdict is None:
        _deny(
            "git push is gated: no Reviewer verdict on the blackboard yet. "
            "Wait for the Reviewer to publish a review-verdict decision."
        )
    elif not verdict.strip().upper().startswith("GO:"):
        _deny(f"git push is gated: the Reviewer verdict is not GO ({verdict[:120]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
