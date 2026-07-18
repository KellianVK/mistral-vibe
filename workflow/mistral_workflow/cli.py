import json
import shutil
import sys
from importlib import resources
from pathlib import Path

import click

from mistral_workflow.blackboard.store import Blackboard, blackboard_path
from mistral_workflow.planner import role_log_path, run_workflow
from mistral_workflow.roles import WorkflowManifest, load_manifest, manifest_path

DEFAULT_ROLES = ["planner", "backend", "qa", "reviewer"]


def _render_workflow_toml(goal: str, gates: list[str], max_loop_iterations: int) -> str:
    return f"""\
[project]
goal = {json.dumps(goal)}
gates = {json.dumps(gates)}
max_loop_iterations = {max_loop_iterations}

[[roles]]
name = "planner"
agent_profile = "planner"

[[roles]]
name = "backend"
agent_profile = "backend"
depends_on = []

[[roles]]
name = "qa"
agent_profile = "qa"
depends_on = ["backend"]

[[roles]]
name = "reviewer"
agent_profile = "reviewer"
depends_on = ["qa"]
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
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Target project directory (defaults to cwd).",
)
def init(goal: str | None, gates: str, max_loop_iterations: int, project_dir: Path) -> None:
    """Generate .vibe/workflow.toml and copy role agent profiles into the project."""
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    if goal is None:
        goal = click.prompt("Project goal")
    gates_list = [g.strip() for g in gates.split(",") if g.strip()]

    vibe_dir = project_dir / ".vibe"
    agents_dir = vibe_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = manifest_path(project_dir)
    manifest_file.write_text(_render_workflow_toml(goal, gates_list, max_loop_iterations))

    templates_dir = resources.files("mistral_workflow").joinpath("templates/roles")
    for role_name in DEFAULT_ROLES:
        src = templates_dir.joinpath(f"{role_name}.toml")
        dest = agents_dir / f"{role_name}.toml"
        dest.write_text(src.read_text())

    Blackboard(blackboard_path(project_dir)).reset()

    click.echo(f"Wrote {manifest_file}")
    click.echo(f"Wrote {len(DEFAULT_ROLES)} agent profiles to {agents_dir}")
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
    """Start the blackboard API and open the live graph in the browser."""
    click.echo("`mistral workflow graph` lands in Phase 2 (visualizer not built yet).", err=True)
    sys.exit(1)


@workflow.command()
@click.option(
    "--project-dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
)
def loop(project_dir: Path) -> None:
    """Manually trigger the Loop Engine against the manifest's gates."""
    click.echo("`mistral workflow loop` lands in Phase 3 (loop engine not built yet).", err=True)
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
    state = Blackboard(blackboard_path(project_dir)).state()
    if not replay:
        click.echo(json.dumps(state, indent=2))
        return
    click.echo("`mistral workflow memory --replay` lands in Phase 3.", err=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
