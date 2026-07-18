"""Conversational `vibe workflow init`.

Empty directory: a few questions compose a 6-8 agent team and kick off the
run with the live board. Existing project: a scan proposes the team and only
asks for confirmation. `--no-auto-run` provisions everything and stops (the
deterministic mode used by tests and scripted demos).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from vibe.workflow.roles import FULL_TEAM_BASE, select_roles
from vibe.workflow.scanner import ProjectScan, scan_project
from vibe.workflow.setup import configure_workdir

TEAM_CONFIG_NAME = "workflow_team.json"


def team_config_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".vibe" / TEAM_CONFIG_NAME


def load_team_config(workdir: Path) -> dict[str, object] | None:
    path = team_config_path(workdir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_team_config(workdir: Path, goal: str, roles: list[str], strict: bool) -> None:
    path = team_config_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"goal_default": goal, "roles": roles, "strict_quality": strict}, indent=2
        ),
        encoding="utf-8",
    )


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{suffix} ").strip()
    except EOFError as error:
        raise RuntimeError(
            "init is interactive; run it in a terminal or use --no-auto-run "
            "with --goal for scripted setups"
        ) from error
    return answer or default


def _ask_yes_no(question: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{question} ({hint})").lower()
    if not answer:
        return default
    return answer.startswith("y") or answer.startswith("o")


def compose_team(
    *, wants_frontend: bool, wants_devops: bool, scan: ProjectScan | None = None
) -> list[str]:
    """Always Planner+Reviewer; base Backend/QA/Security/Docs; 6-8 agents."""
    team = list(FULL_TEAM_BASE)
    if wants_frontend or (scan is not None and scan.has_frontend):
        team.insert(3, "Frontend")
    if wants_devops or (scan is not None and scan.has_ci):
        team.append("DevOps")
    return team


def run_init(
    workdir: Path, *, auto_run: bool, board_port: int, goal_override: str | None = None
) -> int:
    resolved = workdir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    scan = scan_project(resolved)

    print("MiaouFlow — team setup")
    if scan.is_empty:
        goal = goal_override or _ask("What do you want to build?")
        if not goal:
            print("A goal is required.", file=sys.stderr)
            return 1
        wants_frontend = _ask_yes_no("Does it need a web UI?", default=True)
        stack = _ask("Any imposed stack? (empty = MiaouFlow chooses)")
        strict = _ask_yes_no("Strict quality/security bar?", default=False)
        wants_devops = _ask_yes_no("Also CI/deploy setup?", default=False)
        if stack:
            goal = f"{goal} Use this stack: {stack}."
        team = compose_team(wants_frontend=wants_frontend, wants_devops=wants_devops)
    else:
        print(f"  Scanned: {scan.summary()}")
        goal = goal_override or _ask("What is the goal of this session?")
        if not goal:
            print("A goal is required.", file=sys.stderr)
            return 1
        proposed = compose_team(wants_frontend=False, wants_devops=False, scan=scan)
        answer = _ask(
            f"Proposed team: {', '.join(proposed)}. Edit the list or press "
            "Enter to accept.",
            default=",".join(proposed),
        )
        team = [name.strip() for name in answer.split(",") if name.strip()]
        strict = _ask_yes_no("Strict quality/security bar?", default=False)

    if strict:
        goal = (
            f"{goal} Quality bar: STRICT — Security and QA must fail the run "
            "on any marginal finding rather than waving it through."
        )

    roles = select_roles(team, workdir=resolved)
    configure_workdir(resolved, roles)
    save_team_config(resolved, goal, [role.name for role in roles], strict)
    print(f"  Team of {len(roles)}: {', '.join(role.name for role in roles)}")
    print(f"  Saved to {team_config_path(resolved)}")

    if not auto_run:
        print("  Next: vibe workflow run --workdir", resolved)
        return 0

    from vibe.workflow.orchestrator import run_workflow_command

    return run_workflow_command(
        goal=goal,
        workdir=resolved,
        role_names=[role.name for role in roles],
        board_port=board_port,
        open_browser=True,
    )
