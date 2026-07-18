"""CLI entrypoint for `vibe workflow` (MiaouFlow)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

DEFAULT_BOARD_PORT = 8787


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe workflow",
        description=(
            "MiaouFlow: spawn a team of Vibe agents that coordinate through a "
            "shared blackboard, with a live web board."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Conversational setup: compose a 6-8 agent team (scan-driven on an "
            "existing project), then run with the live board automatically"
        ),
    )
    init_parser.add_argument("--workdir", type=Path, default=Path.cwd())
    init_parser.add_argument(
        "--goal", default=None, help="Skip the goal question (useful for scripts)"
    )
    init_parser.add_argument(
        "--no-auto-run",
        action="store_true",
        help="Provision the team and stop instead of launching run + board",
    )
    init_parser.add_argument("--port", type=int, default=DEFAULT_BOARD_PORT)

    run_parser = subparsers.add_parser(
        "run", help="Run a workflow: planner first, then implementers in parallel"
    )
    run_parser.add_argument("--goal", required=True, help="Goal shared by all roles")
    run_parser.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Target project directory (default: current directory)",
    )
    run_parser.add_argument(
        "--roles",
        default=None,
        help=(
            "Comma-separated roles to run (default: Planner,Backend,Frontend; "
            "add QA for a verification wave)"
        ),
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Global wall-clock budget in seconds (default: 600)",
    )
    run_parser.add_argument(
        "--no-warm-start",
        action="store_true",
        help="Skip injecting the shared warm-start brief into agent prompts",
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_BOARD_PORT,
        help=f"Port for the live MiaouFlow board (default: {DEFAULT_BOARD_PORT})",
    )
    run_parser.add_argument(
        "--no-board", action="store_true", help="Do not start the live web board"
    )
    run_parser.add_argument(
        "--max-loop-iterations",
        type=int,
        default=3,
        help="Max QA-fail retry rounds of the quality loop (default: 3; 0 disables)",
    )

    board_parser = subparsers.add_parser(
        "board", help="Serve the MiaouFlow board for an existing workflow directory"
    )
    board_parser.add_argument("--workdir", type=Path, default=Path.cwd())
    board_parser.add_argument("--port", type=int, default=DEFAULT_BOARD_PORT)

    status_parser = subparsers.add_parser(
        "status", help="Print the blackboard status snapshot"
    )
    status_parser.add_argument("--workdir", type=Path, default=Path.cwd())
    return parser


def _run_status(workdir: Path) -> int:
    from vibe.workflow.setup import workflow_database_path
    from vibe.workflow.store import read_board_state

    database_path = workflow_database_path(workdir)
    if not database_path.exists():
        print(f"No workflow database at {database_path}", file=sys.stderr)
        return 1

    state = read_board_state(database_path)
    goal = state.get("goal")
    print(f"goal: {goal if goal else '(no run yet)'}")
    agents: dict[str, dict[str, str]] = state["agents"]
    for role, agent in sorted(agents.items()):
        print(f"  {role:10s} {agent['status']:8s} {agent['current_task']}")
    print(f"decisions: {len(state['decisions'])}  questions: {len(state['questions'])}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        from vibe.workflow.init_flow import run_init

        try:
            return run_init(
                args.workdir,
                auto_run=not args.no_auto_run,
                board_port=args.port,
                goal_override=args.goal,
            )
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    if args.command == "status":
        return _run_status(args.workdir)

    if args.command == "board":
        from vibe.workflow.server import serve_board

        return serve_board(args.workdir, port=args.port)

    from vibe.workflow.orchestrator import run_workflow_command

    roles = (
        [name.strip() for name in args.roles.split(",") if name.strip()]
        if args.roles
        else None
    )
    return run_workflow_command(
        goal=args.goal,
        workdir=args.workdir,
        role_names=roles,
        timeout_seconds=args.timeout,
        warm_start=not args.no_warm_start,
        board_port=None if args.no_board else args.port,
        max_loop_iterations=args.max_loop_iterations,
    )


if __name__ == "__main__":
    raise SystemExit(main())
