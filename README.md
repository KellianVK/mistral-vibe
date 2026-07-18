# mistral workflow

Orchestrates a team of persistent [Mistral Vibe](https://github.com/KellianVK/mistral-vibe) agent
sessions (Planner, Backend, QA, Reviewer) on a project, with a shared Blackboard for
cross-agent memory and a live ReactFlow graph of the team's status in the browser.

```
mistral workflow init   → generates .vibe/workflow.toml + agent profiles for a target project
mistral workflow run    → runs the team; a failing QA gate retries Backend, bounded by max_loop_iterations
mistral workflow graph  → live team graph at http://localhost:5173
```

## Layout

| Path | What |
|---|---|
| [`mistral-vibe/`](mistral-vibe/) | Vendored fork this builds on. Untouched — see [NOTES-FORK.md](NOTES-FORK.md) for how it's driven. |
| [`workflow/`](workflow/) | `mistral-workflow` Python package: the CLI, orchestrator, and Blackboard. |
| [`visualizer/`](visualizer/) | Vite + React + `@xyflow/react` app showing the team graph live. |
| [`demo-project/`](demo-project/) | A small target project pre-wired with a demo scenario (see below). |
| [`NOTES-FORK.md`](NOTES-FORK.md) | Reconnaissance notes on the fork's headless mode, agent profiles, MCP, hooks. |

## One-time setup

Requires `uv` (Python) and Node 20+. Vibe itself must already be authenticated against a
model (`~/.vibe/config.toml` — run `vibe` once interactively first if you haven't).

```bash
uv tool install --editable ./workflow   # installs `mistral` and `mistral-workflow-api`
(cd visualizer && npm install)
```

## Replay the demo (2 commands)

The repo ships with `demo-project/` already `init`-ed with a scenario built to show the
quality loop: `calculator.py` has an `add(a, b)` that returns `a - b` (a real, deliberate bug),
and the goal asks the team to add a `multiply` function without touching `add`. Backend leaves
the existing bug alone (it wasn't asked to touch it); QA writes independent tests from the
function's name/spec, catches it, and Backend gets one more pass with the failure in hand.

```bash
cd demo-project && mistral workflow graph   # starts the API + visualizer, opens the browser
mistral workflow run                        # watch the graph animate live
```

To replay from scratch (fresh goal, fresh bug):

```bash
cd demo-project
rm -f calculator.py test_calculator.py && rm -rf .vibe/logs .vibe/workflow-state.json
printf 'def add(a, b):\n    return a - b\n' > calculator.py
mistral workflow init --goal "There is an existing calculator.py with an add(a, b) function used elsewhere in the codebase — do not modify add() itself. Add a multiply(a, b) function to calculator.py, matching the existing code style. Tests must cover every function in the module, both new and pre-existing."
mistral workflow run
```

Other commands: `mistral workflow status`, `mistral workflow agents`, `mistral workflow logs
--agent <role>`, `mistral workflow memory` (raw Blackboard JSON), `mistral workflow loop`
(manually retry a currently-blocked QA gate).

## How it works

- **No pty hacking.** `vibe -p` is a first-class headless mode in this fork — see
  [NOTES-FORK.md](NOTES-FORK.md). `vibe_runner.py` shells out to it per role
  (`--agent <role> --trust --auto-approve --output json`), one subprocess per turn.
- **Roles are plain Vibe agent profiles.** `mistral workflow init` copies
  `workflow/mistral_workflow/templates/roles/*.toml` into the target project's
  `.vibe/agents/`. Each is just `bypass_tool_permissions = true` plus a description — role
  identity and task framing live in `planner.py`'s prompts, not in the TOML, so they're easy
  to read and change in one place.
- **The Blackboard is a JSON file**, not a database or an MCP server. `mistral workflow run`
  (one process) is the only writer; `blackboard/api.py` (a separate FastAPI process) only
  reads it. Atomic writes (temp file + `os.replace`) mean the API never sees a partial write.
- **The visualizer prefers a WebSocket**, falling back to polling `GET /state` every 1.5s if
  the socket won't connect, and keeps retrying the socket in the background.
- **The Loop Engine** (`loop_engine.py`) only loops the QA↔Backend edge: if QA doesn't report
  `RESULT: PASS`, it republishes the full test failure to the Blackboard, re-runs Backend
  (which now sees that failure as context), then re-runs QA — up to `max_loop_iterations`
  times before marking the gate `blocked` and stopping for good. It never retries forever.

## Simplifications assumed (documented here rather than hidden)

- **Orchestration is external.** Roles are driven from outside each Vibe session via
  subprocess, not via Vibe's own `task`-tool subagent delegation. Simpler to build and to
  reason about in the time available; see NOTES-FORK.md Q3 for the alternative.
- **Blackboard access is orchestrator-side, not agent-side.** Roles don't call an MCP tool or
  hit an HTTP endpoint themselves to read/write the Blackboard — the Python orchestrator does
  it around each subprocess call. A real MCP stdio server for the Blackboard
  (`blackboard/mcp_server.py`) was scoped as a Phase 4 stretch goal and traded for reliability
  under time pressure.
- **The Loop Engine only closes the QA↔Backend loop**, not Reviewer. A Reviewer `NO-GO`
  currently gets logged as a decision but does not block the run or trigger a retry — worth
  knowing if you see "completed successfully" alongside a `NO-GO` decision in the log.
- **Execution is sequential**, even for roles with no shared dependency. Simpler to reason
  about and, for a live demo, more legible than concurrent updates racing in the graph.
- **No per-role model override is wired up end-to-end.** `workflow.toml`'s `model` field is
  parsed but currently unused; every role inherits whatever model/provider is already active
  in `~/.vibe/config.toml`. Hardcoding specific model names (as the original spec's example
  does) is environment-specific and risks a demo failing on a machine configured differently.

## Known rough edges

- `mistral workflow graph` locates `visualizer/` relative to its own installed source
  (assumes the `workflow/` and `visualizer/` sibling layout in this repo — see
  `cli.py`'s `graph` command). Moving `workflow/` out of this repo breaks that lookup.
- The Blackboard has no cross-process lock beyond atomic writes — fine for one `run` at a
  time (the intended usage), not for two concurrent `run`s against the same project.
