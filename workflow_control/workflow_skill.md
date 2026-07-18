---
name: workflow
description: Start, inspect, or stop the Planner, Backend, and QA workflow from Vibe.
user-invocable: true
allowed-tools: start_workflow get_workflow_status stop_workflow
---

# Workflow control

<!-- Managed by vibe-workflow. -->

Interpret the text after `/workflow` as follows:

- If it is `status`, call `get_workflow_status` exactly once and
  summarize the controller state plus each agent's state and current task.
- If it is `stop` or `cancel`, call `stop_workflow` exactly once
  and report whether a running workflow was stopped.
- Otherwise, treat the complete text as the software-delivery goal and call
  `start_workflow` exactly once with that goal. If the goal is
  empty, ask the user for it without calling a tool.

The workflow starts in the background. Never launch `vibe-workflow`, `vibe`, or
the orchestrator through `bash`, and never poll repeatedly in one turn. After a
successful start, tell the user to use `/workflow status` whenever they want a
snapshot or `/workflow stop` to cancel it.
