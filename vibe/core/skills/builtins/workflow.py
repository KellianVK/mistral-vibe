from __future__ import annotations

from vibe.core.skills.models import SkillInfo

_PROMPT = """# MiaouFlow — drive an agent team from this session

The user invoked /workflow. You control MiaouFlow (this fork's multi-agent
orchestration layer) through three native tools — never reimplement any of
this with bash:

- `start_workflow(goal)` — spawns the team in the background (Planner first,
  then implementers in parallel waves; the quality loop retries on a QA
  failure). Also starts the live board and returns its URL.
- `get_workflow_status()` — run state plus per-agent status snapshot.
- `stop_workflow()` — cancels the background run.

## How to respond to the invocation

- `/workflow <goal text>` -> call `start_workflow` with the goal verbatim.
  Reply with one short confirmation that MUST include the board URL from the
  tool result so the user can watch live.
- `/workflow status` (or the user asks how it's going) -> call
  `get_workflow_status` and summarize: overall state, then one line per
  agent (role, state, current task). Do not paste raw JSON.
- `/workflow stop` -> call `stop_workflow` and confirm.

## Rules

1. One run at a time — if `start_workflow` reports one is already running,
   tell the user and offer status or stop.
2. The run continues in the background; do NOT block or poll in a loop.
   Check status only when the user asks (or once after a few minutes if
   they ask you to babysit it).
3. The team works in the CURRENT directory: agents read and write files
   here and coordinate through `.vibe/workflow.db`. Warn the user before
   starting if the directory looks like it contains unrelated
   uncommitted work.
4. Roles: the full 8-agent team by default (Planner, Backend, Frontend,
   QA, Security, DevOps, Docs, Reviewer), or the team composed by
   `vibe workflow init` when `.vibe/workflow_team.json` exists here. A full
   run takes ~10-15 min and up to ~16 $; suggest `vibe workflow run
   --roles Planner,Backend,Frontend --goal ...` in a terminal when the user
   wants a quick/cheap run.
5. Costs money and takes minutes (a small goal ≈ 2-4 min, ~2-6 $). Say so
   when starting."""

SKILL = SkillInfo(
    name="workflow",
    description=(
        "Launch and control a MiaouFlow multi-agent team (Planner, Backend, "
        "Frontend, ...) from inside this session: /workflow <goal>, "
        "/workflow status, /workflow stop. Live board included."
    ),
    user_invocable=True,
    allowed_tools=["start_workflow", "get_workflow_status", "stop_workflow"],
    prompt=_PROMPT,
)
