---
name: workflow
description: Run and inspect the mistral-workflow multi-agent team (Planner/Backend/QA/Reviewer, optionally Security/DevOps/Frontend/Docs) for this project without leaving the current session. Use when the user types /workflow, or asks to run/check/init the team, or asks about the Blackboard, team status, blocked gates, or the live graph.
metadata:
  display-name: Mistral Workflow
  short-description: Run the multi-agent team from inside Vibe
  default-prompt: Use $workflow to run or inspect the multi-agent team for this project.
---

# Mistral Workflow

`mistral workflow` orchestrates a team of separate Vibe sessions (Planner, Backend, QA,
Reviewer — optionally Security, DevOps, Frontend, Docs) on this project, coordinating through a
shared Blackboard file. It is a standalone CLI that spawns its own `vibe -p` subprocesses — you
drive it with the `bash` tool, you do not reimplement any of its logic yourself.

## Commands

All of these are fast (read a local JSON file, no LLM calls) and safe to run directly:

- `mistral workflow status` — current agent statuses + recent decisions, text output.
- `mistral workflow agents` — list configured roles and their depends_on.
- `mistral workflow logs --agent <role>` — raw transcript of a role's last turn.
- `mistral workflow memory --replay` — the Blackboard rendered as a readable narrative.
- `mistral workflow loop` — manually retry a currently-blocked QA gate.

These two are slower and need different handling:

- `mistral workflow init --goal "..."` — one-time setup, writes `.vibe/workflow.toml` +
  agent profiles. Only needed if `.vibe/workflow.toml` doesn't already exist in this project.
- `mistral workflow run` — runs the whole team, sequentially, one role at a time. **This can
  take several minutes** (each role is a real LLM turn, sometimes several). Call it with an
  explicit longer bash timeout (`timeout: 600`, the maximum this environment allows) rather than
  the default. If it still doesn't finish in time for a larger team or a QA retry loop, that's a
  known limit, not a bug — tell the user, then use `mistral workflow status` to report how far
  it got. Do not try to background it with `nohup` or a bare `&`; this environment blocks
  standalone `nohup` and a `&`-backgrounded process is not reliably preserved once the tool call
  returns. If a run genuinely needs more than ~10 minutes, tell the user to run
  `mistral workflow run` themselves in a separate terminal instead of through this session.

`mistral workflow graph` starts a local FastAPI server + Vite dev server and opens a browser —
only run this if the user is at a machine with a browser and explicitly wants the live visual
dashboard, not from a fully headless environment.

## When invoked as /workflow

1. If `.vibe/workflow.toml` doesn't exist in the current project, ask the user for a goal and
   run `mistral workflow init --goal "..."` before anything else.
2. If the user just types `/workflow` with no further instruction, run `mistral workflow status`,
   summarize it in your own words (who's working, who's blocked and why, latest decision), and
   ask what they want to do next.
3. If they ask to run/start/kick off the team, run `mistral workflow run` as described above.
4. When reporting Blackboard content (decisions, blocked gates, open questions), summarize it —
   don't just paste raw command output back at the user.
