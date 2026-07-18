from __future__ import annotations

from vibe.core.skills.models import SkillInfo

_PROMPT = """# Workflow control

Supported forms include `/workflow <goal>`, `/workflow status`,
`/workflow dashboard`, and `/workflow stop`.

Interpret the text after `/workflow` as follows:

- If it is `status`, call `get_workflow_status` exactly once and summarize the
  controller state plus each agent's state and current task. Treat the
  controller state as authoritative: when it is `running`, say the workflow is
  still running even if an individual agent is blocked. Mention that agent as
  a partial failure without calling the whole workflow blocked.
- If it is `dashboard`, call `open_workflow_dashboard` exactly once. Report
  whether the browser opened and include the returned localhost URL so it can
  be opened manually when necessary.
- If it is `stop` or `cancel`, call `stop_workflow` exactly once and report
  whether a running workflow was stopped.
- Otherwise, treat the complete text as the software-delivery goal and call
  `start_workflow` exactly once with that goal. If the goal is empty, ask the
  user for it without calling a tool.

The workflow starts in the background. Never launch `vibe-workflow`, `vibe`, or
the orchestrator through `bash`, and never poll repeatedly in one turn. Never
retry a workflow control tool in the same turn, even when it fails; report the
error instead. After a successful start, tell the user to use `/workflow status`
whenever they want a snapshot or `/workflow stop` to cancel it.
"""

SKILL = SkillInfo(
    name="workflow",
    description=(
        "Start, inspect, or stop the built-in Planner, Backend, and QA workflow."
    ),
    allowed_tools=[
        "start_workflow",
        "get_workflow_status",
        "open_workflow_dashboard",
        "stop_workflow",
    ],
    user_invocable=True,
    prompt=_PROMPT,
)
