# mistral workflow

A companion package living alongside [Mistral Vibe](README.md) in this repo (`workflow/`,
`visualizer/`, `demo-project/`, this file) that orchestrates a team of persistent Vibe agent
sessions (Planner, Backend, QA, Reviewer — plus optional Security, DevOps, Frontend, Docs) on a
project, with a shared Blackboard for cross-agent memory and a live ReactFlow graph of the
team's status in the browser. It doesn't modify Vibe itself — see [NOTES-FORK.md](NOTES-FORK.md)
for how it drives Vibe from the outside.

```
mistral workflow init   → generates .vibe/workflow.toml + agent profiles for a target project
mistral workflow run    → runs the team; a failing QA gate retries Backend, bounded by max_loop_iterations
mistral workflow graph  → live team graph at http://localhost:5173
```

## Layout

| Path | What |
|---|---|
| Everything else at the repo root (`vibe/`, `tests/`, `pyproject.toml`, ...) | Mistral Vibe itself — untouched, see [README.md](README.md) for the CLI and [NOTES-FORK.md](NOTES-FORK.md) for how this package drives it. |
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
# QA names its own test file each run, so glob rather than hardcode one name:
rm -f calculator.py *test*.py && rm -rf .vibe/logs .vibe/workflow-state.json __pycache__
printf 'def add(a, b):\n    return a - b\n' > calculator.py
mistral workflow init --goal "There is an existing calculator.py with an add(a, b) function used elsewhere in the codebase — do not modify add() itself. Add a multiply(a, b) function to calculator.py, matching the existing code style. Tests must cover every function in the module, both new and pre-existing."
mistral workflow run
```

Other commands: `mistral workflow status`, `mistral workflow agents`, `mistral workflow logs
--agent <role>`, `mistral workflow memory` (raw Blackboard JSON) / `--replay` (readable
narrative), `mistral workflow loop` (manually retry a currently-blocked QA gate).

### Using it from inside `vibe` itself

`mistral workflow init` also writes `.vibe/skills/workflow/SKILL.md`, so once a project is
initialized you don't have to leave an interactive `vibe` session to drive the team — type
`/workflow` and it runs `mistral workflow status`/`run`/etc. via `bash` on your behalf and
reports back in its own words. Verified live: `/workflow` correctly ran `status` and summarized
it, and correctly called `mistral workflow update_status` over the `blackboard_*` MCP tools when
asked to. The one thing to know: `mistral workflow run` can take several minutes, and this
environment's `bash` tool caps at a 600s timeout with `nohup`/backgrounding blocked — the skill
tells the model to raise the call's timeout to the max and, if a bigger team still doesn't
finish in time, to say so and suggest running it in a separate terminal instead. That fallback
path is documented, not exhaustively re-verified end-to-end under this session's time budget.

### Bonus roles

`mistral workflow init --extra-roles security,devops,frontend,docs` adds any subset of four
extra roles to the team. Topology (edges to a role you didn't select are dropped):

```
planner    backend
             ├── frontend ──┐
             └── security ──┼── qa ── devops
                             └── reviewer ── docs
```
`frontend`/`security` run once `backend` is done; `qa` waits on both `backend` and `frontend`;
`reviewer` waits on `qa` and `security`; `devops`/`docs` run last. Verified structurally
(manifest parses, topological order resolves correctly) and against a live single-role session
per role type — not re-run as a full 8-role live demo by default, since the 4-role scenario is
the one built for reliability under demo conditions.

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
- **The visualizer's visual language is derived from the real Vibe CLI**, not invented: colors
  extracted from `vibe/cli/textual_ui/app.tcss` and rendered Vibe snapshots under
  `tests/snapshots/`, not just the brief's description of them. Components: `WorkflowHeader`
  (goal, run status, connection, completion count), `TeamCanvas` (the graph, one `AgentNode` per
  role — stable dimensions, clamped task text so long labels never reflow the graph),
  `DecisionFeed` / `OpenQuestions` (right rail), `ClaimPanel` (bottom strip). `buildGraph()` in
  `lib/buildGraph.ts` is a pure `(manifest, state) -> {nodes, edges}` function with fixed,
  hardcoded positions per canonical role — no auto-layout dependency, no reflow as roles
  complete. Open questions render as animated, labeled graph edges (`waiting on X`) distinct
  from the static dependency edges; when a question and a dependency connect the same two roles,
  the dependency's label is suppressed rather than overlapping the more specific question label
  (found and fixed by actually looking at the rendered graph, not just reading the code).
- **The visualizer prefers a WebSocket**, falling back to polling `GET /state` every 1.5s if
  the socket won't connect. Connection state is a real three-value model (`live` /
  `reconnecting` / `offline`, promoted to `offline` after 3 consecutive failed polls), and the
  last known snapshot is always retained — verified by killing the API server against a
  production build (`vite preview`, no dev-server HMR to confound the test) and confirming the
  goal/graph/decisions stayed on screen with the header correctly reading "Offline", then
  recovering to "Live" on restart with no page reload needed.
- **File claims are a real, additive Blackboard concept** (`claim_file`/`release_file` on
  `Blackboard`, exposed as `blackboard_*` MCP tools), not just a UI mock — but nothing in the
  sequential orchestrator calls them automatically, so `ClaimPanel` will show "No active claims"
  in an ordinary `mistral workflow run`. It's there for agents that choose to call
  `blackboard_claim_file`, and was verified by seeding representative conflict data directly,
  the same "additive, best-effort" posture as `blackboard_request_review`.
- **The Loop Engine** (`loop_engine.py`) only loops the QA↔Backend edge: if QA doesn't report
  `RESULT: PASS`, it republishes the full test failure to the Blackboard, re-runs Backend
  (which now sees that failure as context), then re-runs QA — up to `max_loop_iterations`
  times before marking the gate `blocked` and stopping for good. It never retries forever.
- **Agents can also read/write the Blackboard directly.** `mistral workflow init` writes
  `.vibe/config.toml` wiring up `blackboard/mcp_server.py` as a real stdio MCP server
  (`publish_decision`/`read_decisions`/`request_review`/`update_status`, exposed to each
  session as `blackboard_*` tools) and `.vibe/hooks.toml` with a `pre_tool`/`bash` hook that
  denies `git push` until the Blackboard has a `GO` decision from `reviewer`. Both are
  orchestrator-independent — verified with the real MCP client SDK and with a live Vibe
  session actually getting its `git push` denied by the hook, not just by calling the scripts
  directly. The orchestrator's own status/decision writes (reliable, deterministic) still drive
  the core loop; the MCP tools are an additional channel mainly exercised via
  `blackboard_request_review`, which every role is told it may use to ask another role a direct
  question — this part is best-effort (an LLM choosing to call an available tool), unlike the
  deterministic bug-injection loop.

## Simplifications assumed (documented here rather than hidden)

- **Orchestration is external.** Roles are driven from outside each Vibe session via
  subprocess, not via Vibe's own `task`-tool subagent delegation. Simpler to build and to
  reason about in the time available; see NOTES-FORK.md Q3 for the alternative.
- **Blackboard writes that the demo depends on stay orchestrator-side.** Agents *can* call the
  `blackboard_*` MCP tools directly (see above), but role status/decisions/QA-pass-fail — the
  signals the Loop Engine and the visualizer depend on — are always written by the Python
  orchestrator around each subprocess call, not left to an LLM remembering to call a tool.
  MCP is additive, not load-bearing.
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
- `blackboard/api.py`'s CORS allowlist is hardcoded to `localhost`/`127.0.0.1` on ports 5173
  (`vite`/`npm run dev`) and 4173 (`vite preview`). Found the hard way: WebSocket connections
  aren't subject to CORS, so serving the dashboard from any other origin silently breaks the
  plain `GET /manifest` and `/state` fetches (initial load, and the polling fallback) while the
  WS still connects and looks fine — a real dashboard deployed elsewhere needs this list
  extended or relaxed.
- `/workflow`'s guidance for backgrounding a long `mistral workflow run` from inside an
  interactive Vibe session (raise the bash timeout to the max, fall back to a separate terminal
  for bigger teams) is the best available approach given this environment blocks `nohup` and
  caps bash at 600s — but it's model-followed instruction, not code, and wasn't exhaustively
  re-verified for every team size / retry-count combination.
