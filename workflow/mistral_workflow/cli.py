import json
import sys
from importlib import resources
from pathlib import Path

import click

from mistral_workflow.blackboard.store import Blackboard, blackboard_path
from mistral_workflow.loop_engine import retry_qa_gate, run_workflow
from mistral_workflow.planner import role_log_path
from mistral_workflow.roles import WorkflowManifest, load_manifest, manifest_path

CORE_ROLES = ["planner", "backend", "qa", "reviewer"]
EXTRA_ROLES = ["security", "devops", "frontend", "docs"]

# Static depends_on topology for the full role set. Edges pointing at a role
# that isn't active (an extra role the caller didn't select) are dropped, so
# e.g. with no extras qa's edges collapse to just ["backend"].
ROLE_DEPENDS_ON: dict[str, list[str]] = {
    "planner": [],
    "backend": [],
    "frontend": ["backend"],
    "security": ["backend"],
    "qa": ["backend", "frontend"],
    "devops": ["qa"],
    "reviewer": ["qa", "security"],
    "docs": ["reviewer"],
}
# Declaration order for the manifest — keeps a stable, readable role list.
ALL_ROLES = ["planner", "backend", "frontend", "security", "qa", "devops", "reviewer", "docs"]


def _render_workflow_toml(
    goal: str, gates: list[str], max_loop_iterations: int, extra_roles: list[str]
) -> str:
    active = set(CORE_ROLES) | set(extra_roles)
    lines = [
        "[project]",
        f"goal = {json.dumps(goal)}",
        f"gates = {json.dumps(gates)}",
        f"max_loop_iterations = {max_loop_iterations}",
    ]
    for role in (r for r in ALL_ROLES if r in active):
        deps = [dep for dep in ROLE_DEPENDS_ON[role] if dep in active]
        lines += ["", "[[roles]]", f'name = "{role}"', f'agent_profile = "{role}"', f"depends_on = {json.dumps(deps)}"]
    return "\n".join(lines) + "\n"


def _mcp_config_toml(project_dir: Path) -> str:
    return f"""\
[[mcp_servers]]
name = "blackboard"
transport = "stdio"
command = "mistral-workflow-mcp"
env = {{ "MISTRAL_WORKFLOW_PROJECT_DIR" = {json.dumps(str(project_dir))} }}
"""


def _hooks_config_toml() -> str:
    return """\
[[hooks]]
name = "require-reviewer-approval"
type = "pre_tool"
match = "bash"
command = "mistral-workflow-guard-push"
strict = true
timeout = 10.0
description = "Block git push until the Blackboard has a GO decision from reviewer."
"""


def _require_manifest(project_dir: Path) -> WorkflowManifest:
    path = manifest_path(project_dir)
    if not path.exists():
        click.echo(f"No workflow manifest at {path}. Run `mistral workflow init` first.", err=True)
        sys.exit(1)
    try:
        return load_manifest(path)
    except Exception as e:
        click.echo(f"Invalid {path}: {e}", err=True)
        sys.exit(1)


@click.group()
def main() -> None:
    pass


@main.group()
def workflow() -> None:
    """Orchestrate a team of Mistral Vibe agents on this project."""


@workflow.command()
@click.option("--goal", default=None, help="Project goal. Prompted for if omitted.")
@click.option("--gates", default="tests", help="Comma-separated quality gates (e.g. tests,lint).")
@click.option("--max-loop-iterations", default=3, show_default=True, type=int)
@click.option(
    "--extra-roles",
    default="",
    help=f"Comma-separated bonus roles to add: {','.join(EXTRA_ROLES)}",
)
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Target project directory (defaults to cwd).",
)
def init(
    goal: str | None, gates: str, max_loop_iterations: int, extra_roles: str, project_dir: Path
) -> None:
    """Generate .vibe/workflow.toml and copy role agent profiles into the project."""
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    if goal is None:
        goal = click.prompt("Project goal")
    gates_list = [g.strip() for g in gates.split(",") if g.strip()]

    extras = [r.strip() for r in extra_roles.split(",") if r.strip()]
    unknown = set(extras) - set(EXTRA_ROLES)
    if unknown:
        click.echo(f"Unknown extra role(s) {sorted(unknown)}. Choose from: {EXTRA_ROLES}", err=True)
        sys.exit(1)
    active_roles = CORE_ROLES + extras

    vibe_dir = project_dir / ".vibe"
    agents_dir = vibe_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = manifest_path(project_dir)
    manifest_file.write_text(_render_workflow_toml(goal, gates_list, max_loop_iterations, extras))

    templates_dir = resources.files("mistral_workflow").joinpath("templates/roles")
    for role_name in active_roles:
        src = templates_dir.joinpath(f"{role_name}.toml")
        dest = agents_dir / f"{role_name}.toml"
        dest.write_text(src.read_text())

    skill_dir = vibe_dir / "skills" / "workflow"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_src = resources.files("mistral_workflow").joinpath("templates/skills/workflow/SKILL.md")
    (skill_dir / "SKILL.md").write_text(skill_src.read_text())

    (vibe_dir / "config.toml").write_text(_mcp_config_toml(project_dir))
    (vibe_dir / "hooks.toml").write_text(_hooks_config_toml())

    Blackboard(blackboard_path(project_dir)).reset()

    click.echo(f"Wrote {manifest_file}")
    click.echo(f"Wrote {len(active_roles)} agent profiles to {agents_dir}")
    click.echo(f"Wrote {vibe_dir / 'config.toml'} (blackboard MCP server)")
    click.echo(f"Wrote {vibe_dir / 'hooks.toml'} (git-push gate)")
    click.echo(f"Wrote {skill_dir / 'SKILL.md'} — use /workflow inside `vibe` in this project")
    click.echo("Run `mistral workflow run` to start the team.")


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def run(project_dir: Path) -> None:
    """Run the team sequentially according to workflow.toml's depends_on graph."""
    project_dir = project_dir.resolve()
    manifest = _require_manifest(project_dir)
    blackboard = Blackboard(blackboard_path(project_dir))

    click.echo(f"Goal: {manifest.project.goal}")
    order = [r.name for r in manifest.execution_order()]
    click.echo(f"Execution order: {' -> '.join(order)}")

    ok = run_workflow(project_dir, manifest, blackboard)

    if ok:
        click.echo("\nWorkflow completed successfully.")
    else:
        click.echo("\nWorkflow stopped — see `mistral workflow status` for details.", err=True)
        sys.exit(1)


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def status(project_dir: Path) -> None:
    """Print the current Blackboard state as text."""
    project_dir = project_dir.resolve()
    state = Blackboard(blackboard_path(project_dir)).state()

    click.echo("Agents:")
    if not state["agents"]:
        click.echo("  (none yet — run `mistral workflow run`)")
    for name, info in state["agents"].items():
        click.echo(f"  {name:12s} {info['status']:8s} {info.get('current_task') or ''}")

    click.echo("\nRecent decisions:")
    for d in state["decisions"][-10:]:
        click.echo(f"  [{d['role']}] {d['summary']}")
    if not state["decisions"]:
        click.echo("  (none yet)")

    open_questions = [q for q in state["questions"] if not q["resolved"]]
    if open_questions:
        click.echo("\nOpen questions:")
        for q in open_questions:
            click.echo(f"  {q['from']} -> {q['to']}: {q['question']}")


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def agents(project_dir: Path) -> None:
    """List the roles defined in workflow.toml."""
    project_dir = project_dir.resolve()
    manifest = _require_manifest(project_dir)
    for role in manifest.roles:
        deps = ", ".join(role.depends_on) or "-"
        click.echo(f"{role.name:12s} agent_profile={role.agent_profile:10s} depends_on={deps}")


@workflow.command()
@click.option("--agent", "agent_name", required=True, help="Role name to show logs for.")
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def logs(agent_name: str, project_dir: Path) -> None:
    """Show the raw output of a role's last session."""
    project_dir = project_dir.resolve()
    path = role_log_path(project_dir, agent_name)
    if not path.exists():
        click.echo(f"No log found for '{agent_name}' at {path}", err=True)
        sys.exit(1)
    click.echo(path.read_text())


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def graph(project_dir: Path) -> None:
    """Start the blackboard API + visualizer dev server and open the live graph."""
    import os
    import socket
    import subprocess
    import time
    import webbrowser

    project_dir = project_dir.resolve()
    _require_manifest(project_dir)

    def port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0

    # workflow/mistral_workflow/cli.py -> workflow/ -> repo root -> visualizer/
    repo_root = Path(__file__).resolve().parent.parent.parent
    visualizer_dir = repo_root / "visualizer"

    if port_open(8787):
        click.echo("Blackboard API already running on http://localhost:8787")
    else:
        env = os.environ.copy()
        env["MISTRAL_WORKFLOW_PROJECT_DIR"] = str(project_dir)
        subprocess.Popen(
            ["mistral-workflow-api"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        click.echo("Started blackboard API on http://localhost:8787")

    if port_open(5173):
        click.echo("Visualizer already running on http://localhost:5173")
    else:
        if not visualizer_dir.exists():
            click.echo(f"Visualizer not found at {visualizer_dir}", err=True)
            sys.exit(1)
        if not (visualizer_dir / "node_modules").exists():
            click.echo(f"Run `npm install` in {visualizer_dir} first.", err=True)
            sys.exit(1)
        subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(visualizer_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        click.echo("Started visualizer dev server on http://localhost:5173")
        time.sleep(2)

    webbrowser.open("http://localhost:5173")


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def loop(project_dir: Path) -> None:
    """Manually retry a blocked QA gate (e.g. after you've poked at the code yourself)."""
    project_dir = project_dir.resolve()
    manifest = _require_manifest(project_dir)
    blackboard = Blackboard(blackboard_path(project_dir))
    state = blackboard.state()

    blocked_qa_roles = [
        role
        for role in manifest.roles
        if role.agent_profile == "qa" and state["agents"].get(role.name, {}).get("status") == "blocked"
    ]
    if not blocked_qa_roles:
        click.echo("No blocked QA gate found — nothing to retry.")
        return

    role = blocked_qa_roles[0]
    click.echo(f"Retrying '{role.name}' gate (up to {manifest.project.max_loop_iterations} iteration(s))...")
    ok = retry_qa_gate(project_dir, manifest, blackboard, role)
    if ok:
        click.echo(f"'{role.name}' now passes.")
    else:
        click.echo(f"'{role.name}' still blocked — see `mistral workflow status`.", err=True)
        sys.exit(1)


@workflow.command()
@click.option(
    "--replay",
    is_flag=True,
    default=False,
    help="Render the Blackboard as a readable narrative instead of raw JSON.",
)
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def memory(replay: bool, project_dir: Path) -> None:
    """Inspect the shared Blackboard."""
    project_dir = project_dir.resolve()
    manifest_file = manifest_path(project_dir)
    goal = load_manifest(manifest_file).project.goal if manifest_file.exists() else None
    state = Blackboard(blackboard_path(project_dir)).state()

    if not replay:
        click.echo(json.dumps(state, indent=2))
        return

    if goal:
        click.echo(f"Team goal: {goal}\n")

    events = [{"kind": "decision", **d} for d in state["decisions"]]
    events += [{"kind": "question", **q} for q in state["questions"]]
    events.sort(key=lambda e: e.get("ts") or "")

    if not events:
        click.echo("Nothing has happened yet — run `mistral workflow run`.")
        return

    for e in events:
        if e["kind"] == "decision":
            if e["role"] == "loop_engine":
                click.echo(f"  ↻ {e['summary']}")
            elif e["summary"].startswith("FAIL:"):
                click.echo(f"  {e['role']} hit a snag: {e['summary'][len('FAIL:'):].strip().splitlines()[0]}")
            elif e["summary"].startswith("ERROR:"):
                click.echo(f"  {e['role']} errored out: {e['summary'][len('ERROR:'):].strip()}")
            else:
                click.echo(f"  {e['role']} → {e['summary']}")
        else:
            resolved = " (resolved)" if e["resolved"] else ""
            click.echo(f"  {e['from']} asked {e['to']}: \"{e['question']}\"{resolved}")

    click.echo("\nCurrent status:")
    for name, info in state["agents"].items():
        click.echo(f"  {name:12s} {info['status']}")


if __name__ == "__main__":
    main()
