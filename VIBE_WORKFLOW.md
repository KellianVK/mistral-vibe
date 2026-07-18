# Vibe Workflow

`vibe-workflow` is a hackathon extension that coordinates three unmodified
Mistral Vibe CLI processes through a shared SQLite blackboard. It does not fork
the Vibe agent loop or import private Vibe APIs: coordination uses a project MCP
server and Vibe's programmatic `--prompt --output streaming` mode.

## Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are recommended. From
this repository's root, install the locked project environment:

```console
uv sync
```

Alternatively, from an activated Python 3.12 virtual environment, install the
checkout in editable mode:

```console
python -m pip install -e .
```

The target project must be a separate, existing directory. Keeping it separate
prevents the Backend and QA workers from editing this Vibe checkout while they
share auto-approved access to their target workdir.

## Configure a target project

Run the setup helper once for each target project, using an absolute path:

```console
uv run setup_workflow.py --workdir "<absolute-path-to-target-project>"
```

With the editable pip installation, run the same helper with
`python setup_workflow.py`. The helper preserves existing valid TOML, appends an
idempotent `workflow` MCP entry to `<workdir>/.vibe/config.toml`, and refuses to
replace an incompatible server that already uses that name. It configures:

```toml
[[mcp_servers]]
name = "workflow"
transport = "stdio"
command = "python"
args = ["-m", "workflow_memory.server"]
env = { "WORKFLOW_DB" = "<absolute-workdir>/workflow.db" }
```

The setup helper escapes the platform-specific path, including Windows paths;
prefer it over copying the TOML by hand.

### Persistently trust the workdir

This step is mandatory for this Vibe checkout. After setup has created the
project `.vibe/config.toml`, start one interactive Vibe session in the target:

```console
uv run vibe --workdir "<absolute-path-to-target-project>"
```

Accept the option that persistently trusts the folder (or its repository), then
exit Vibe. Do not substitute the one-invocation `--trust` flag for this setup
step. Programmatic Vibe never opens the trust dialog: for an untrusted folder it
continues but ignores the project configuration, so the three `workflow_*` MCP
tools would be unavailable to the workers.

## Authentication

`MISTRAL_API_KEY` must be present in the environment that launches the
orchestrator. The orchestrator checks it before starting any worker and passes a
copy of its environment to every Vibe subprocess.

POSIX shells:

```console
export MISTRAL_API_KEY="your-key"
```

PowerShell:

```powershell
$env:MISTRAL_API_KEY = "your-key"
```

## Run the Todo demo

After installation, MCP setup, and persistent trust, the demo is one command:

```console
uv run orchestrator.py run --goal "API Todo en Flask avec 2 endpoints + tests" --workdir "<absolute-path-to-target-project>"
```

The Planner runs first. Backend and QA then run concurrently in the same
workdir; QA polls the blackboard until Backend publishes a decision before it
tests the implementation. The whole workflow has a ten-minute deadline. Each
worker is independently capped at 25 turns and USD 1.00, so the price cap is per
worker rather than USD 1.00 for the complete three-agent run. The maximum
theoretical price for all three workers is therefore USD 3.00.

The current Vibe programmatic mode follows `default_agent`; it is not
unconditionally auto-approved. For headless compatibility, the orchestrator
therefore passes both `--agent auto-approve` and `--auto-approve` explicitly.
This allows file edits, commands, and MCP calls without confirmation. Use a
dedicated target directory and version control for anything valuable.

## Watch status

In a second terminal, keep a live dashboard open while the workflow runs:

```console
uv run orchestrator.py status --workdir "<absolute-path-to-target-project>"
```

Press `Ctrl+C` to stop the dashboard. To print one snapshot and exit:

```console
uv run orchestrator.py status --workdir "<absolute-path-to-target-project>" --once
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
orchestrator.py
  |-- Planner Vibe process ---------\
  |-- Backend Vibe process ----------+--> workflow MCP over stdio --> workflow.db
  `-- QA Vibe process --------------/                                  ^
                                                                        |
dashboard.py (read-only) -----------------------------------------------'
```

### Shared memory

`workflow_memory/server.py` is a FastMCP stdio server backed by
`<workdir>/workflow.db`. Each Vibe process starts its own server process, while
all of them use the same database. SQLite runs in WAL mode; every connection
uses a 5,000 ms busy timeout so short concurrent writes can serialize safely.

The database contains two tables:

- `decisions(id, ts, role, summary, artifact)` stores append-only plans,
  contracts, deliverables, and test findings. `since_id` reads use the
  exclusive lower bound `id > since_id` and are ordered by ascending ID.
- `status(role, state, current_task, updated_at)` stores one upserted row per
  role. Valid states are `working`, `idle`, `blocked`, and `done`.

The database persists between runs and decision counts are cumulative. Use a
fresh target when a demo must start from an empty blackboard. SQLite may also
create `workflow.db-wal` and `workflow.db-shm` while processes are connected;
these files and `logs/` normally belong in the target project's ignore rules.

### MCP tools

The server exposes exactly three tools. Vibe prefixes them with the configured
server name:

- `workflow_read_decisions(filter_role, since_id)` returns decision records and
  must be called before an agent starts work. QA also uses it for incremental
  Backend polling.
- `workflow_publish_decision(role, summary, artifact)` records a meaningful
  step and returns its SQLite ID as a string.
- `workflow_update_status(role, state, current_task)` records lifecycle and
  progress state.

The tool docstrings and role prompts deliberately repeat the protocol: read
first, publish every significant deliverable or interface contract, and update
status at the beginning and end of work.

### Role prompts and sequencing

The role instructions are kept in `prompts/planner.md`, `prompts/backend.md`,
and `prompts/qa.md`. `orchestrator.py` injects the requested goal into each
prompt, launches Vibe with an argument list rather than a shell command, and
parses newline-delimited JSON defensively. Non-JSON stdout lines are ignored.

If a Vibe process cannot start, exits nonzero, is cancelled, or reaches the
global deadline, the orchestrator marks that role `blocked` in SQLite. Other
workers continue when time remains. A zero exit code without a new decision
from that role is also treated as `blocked`, which catches missing or ignored
MCP configuration. A successful process with a published decision is finalized
as `done`.

### Logs

Each run writes per-role output beneath `<workdir>/logs/`:

- `planner.jsonl`, `backend.jsonl`, and `qa.jsonl` contain valid JSON messages
  parsed from Vibe's streaming stdout.
- `planner.stderr.log`, `backend.stderr.log`, and `qa.stderr.log` preserve
  diagnostics, including startup and trust/configuration errors.

Each worker truncates its two log files when it starts. The database, unlike
the logs, is not reset automatically.
