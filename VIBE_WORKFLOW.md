# Vibe Workflow

`vibe-workflow` coordinates three Mistral Vibe CLI processes through a shared
SQLite blackboard. It uses a built-in Skill and tools, an MCP server injected
through Vibe's runtime configuration layer, and Vibe's programmatic
`--prompt --output streaming` mode.

## Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are recommended. From
this repository's root, install the locked project environment:

```console
uv sync
```

The target project must be a separate, existing directory. Keeping it separate
prevents the Backend and QA workers from editing this Vibe checkout while they
share auto-approved access to their target workdir.

## Authentication

The workers use the same Vibe installation, home directory, provider
configuration, environment, and OS keyring as the parent session. Any
authentication method already supported by Vibe therefore works without
workflow-specific setup. Their active model is pinned at runtime to
`mistral-medium-3.5`; this does not modify the parent session or persisted user
configuration. The inherited `VIRTUAL_ENV` is removed before workers start so
dependency commands in a generated project cannot mutate Vibe's environment.

## Run from the interactive Vibe CLI

Start Vibe from any target project. During development, select this checkout
with `uv --project`:

```console
cd <target-project>
uv run --project <path-to-mistral-vibe> vibe
```

`/workflow` is built in and appears in the slash-command menu without a project
`.vibe` directory or setup command. Start a run without blocking the TUI:

```text
/workflow API Todo en Flask avec 2 endpoints + tests
```

The Planner, Backend, and QA agents continue in the background while the Vibe
session remains available. Inspect or cancel that run from the same CLI:

```text
/workflow status
/workflow dashboard
/workflow stop
```

`/workflow dashboard` starts a read-only server bound to `127.0.0.1` on an
available port and opens its tokenized URL in the default browser. The packaged
React Flow view refreshes every second and requires no Node.js installation at
runtime. `/workflow status` remains terminal-only and never opens a window.

Keep the Vibe session open until the workflow finishes. The in-process control
tools allow only one background run per Vibe session and are hidden from
orchestrated child workers to prevent recursive workflow launches.

## Run the Todo demo

The standalone entry point remains available for scripts and CI:

```console
uv run vibe-workflow run --goal "API Todo en Flask avec 2 endpoints + tests" --workdir "<absolute-path-to-target-project>"
```

Planner, Backend, and QA run sequentially in the same workdir. Each downstream
role starts only after its dependency publishes a decision, so QA never spends
model calls polling while Backend works. The workflow has a thirty-minute
deadline and each worker is capped at 50 turns and USD 5.00, so the maximum
theoretical price for all three workers is USD 15.00. Planner only receives read
and coordination tools. If Planner or Backend fails before publishing its
deliverable, dependent roles are blocked without launching.

The current Vibe programmatic mode follows `default_agent`; it is not
unconditionally auto-approved. For headless compatibility, the orchestrator
therefore passes both `--agent auto-approve` and `--auto-approve` explicitly.
This allows file edits, commands, and MCP calls without confirmation. Use a
dedicated target directory and version control for anything valuable.

## Watch standalone status

In a second terminal, keep a live dashboard open while the workflow runs:

```console
uv run vibe-workflow status --workdir "<absolute-path-to-target-project>"
```

Press `Ctrl+C` to stop the dashboard. To print one snapshot and exit:

```console
uv run vibe-workflow status --workdir "<absolute-path-to-target-project>" --once
```

The uncoupled dashboard entry point exposes the same modes:

```console
uv run dashboard.py --workdir "<absolute-path-to-target-project>"
uv run dashboard.py --workdir "<absolute-path-to-target-project>" --once
```

`WORKFLOW_DB` overrides the path derived from `--workdir` for the standalone
dashboard. The dashboard only opens an existing database for reading; before
the first status update it displays `No agent status yet`.

## Architecture

```text
interactive Vibe -> /workflow Skill -> built-in control tools
                                         |
                                         v
                                    orchestrator.py
                                          |
                                          v
                           Planner -> Backend -> QA ----> workflow MCP --> workflow.db
                                                                               ^
                                                                               |
dashboard.py (read-only) ------------------------------------------------------'
```

### Shared memory

`workflow_memory/server.py` is a FastMCP stdio server backed by
`<workdir>/workflow.db`. The orchestrator injects its server definition through
the `VIBE_MCP_SERVERS` runtime configuration for child processes only; it never
creates or modifies project `.vibe` files. Each Vibe process starts its own
server process, while all of them use the same database. SQLite runs in WAL
mode; every connection uses a 5,000 ms busy timeout so short concurrent writes
can serialize safely.

The database contains two tables:

- `decisions(id, ts, role, summary, artifact)` stores append-only plans,
  contracts, deliverables, and test findings. `since_id` reads use the
  exclusive lower bound `id > since_id` and are ordered by ascending ID.
- `status(role, state, current_task, updated_at)` stores one upserted row per
  role. Valid states are `working`, `idle`, `blocked`, and `done`.

The database file persists, but decisions and statuses are cleared atomically
when a new workflow starts so agents never consume stale coordination data.
SQLite may also create `workflow.db-wal` and `workflow.db-shm` while processes
are connected; these files and `logs/` normally belong in the target project's
ignore rules.

### MCP tools

The memory server still exposes exactly three tools. Vibe prefixes them with the
configured server name:

- `workflow_read_decisions(filter_role, since_id)` returns decision records and
  must be called before an agent starts work.
- `workflow_publish_decision(role, summary, artifact)` records a meaningful
  step and returns its SQLite ID as a string.
- `workflow_update_status(role, state, current_task)` records lifecycle and
  progress state.

The tool docstrings and role prompts deliberately repeat the protocol: read
first, publish every significant deliverable or interface contract, and update
status at the beginning and end of work.

The built-in workflow integration exposes four user-facing control tools:

- `start_workflow(goal)` starts orchestration in a background
  task and returns immediately.
- `get_workflow_status()` returns controller state and the
  latest SQLite snapshot for every role.
- `open_workflow_dashboard()` starts or reuses the local read-only dashboard
  and opens its tokenized URL in the default browser.
- `stop_workflow()` cancels the run owned by the current Vibe
  session and terminates its active worker processes.

The `/workflow` Skill maps `run`, `status`, `dashboard`, and `stop` requests to
those tools. It never shells out to a nested interactive Vibe process.

### Role prompts and sequencing

The role instructions are kept in `prompts/planner.md`, `prompts/backend.md`,
and `prompts/qa.md`. `orchestrator.py` injects the requested goal into each
prompt, launches Vibe with an argument list rather than a shell command, and
parses newline-delimited JSON defensively. Non-JSON stdout lines are ignored.

If a Vibe process cannot start, exits nonzero, is cancelled, or reaches the
global deadline, the orchestrator marks that role `blocked` in SQLite and does
not launch roles that depend on its output. A zero exit code without a new
decision from that role is also treated as `blocked`, which catches missing or
ignored MCP configuration. A successful process with a published decision is
finalized as `done`. A worker that publishes its required decision and reports
`done` also remains successful if Vibe reaches its turn limit while producing
the final headless response.

### Logs

Each run writes per-role output beneath `<workdir>/logs/`:

- `planner.jsonl`, `backend.jsonl`, and `qa.jsonl` contain valid JSON messages
  parsed from Vibe's streaming stdout.
- `planner.stderr.log`, `backend.stderr.log`, and `qa.stderr.log` preserve
  diagnostics, including startup and trust/configuration errors.

Each worker truncates its two log files when it starts. Tagged Vibe stop
reasons, such as turn or price limits, are surfaced directly in workflow
status. The database's coordination rows are reset when the workflow starts,
while the database file itself is retained.
