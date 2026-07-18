from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from io import TextIOWrapper
import os
from pathlib import Path
import sqlite3
import sys
import time

from rich.console import Console
from rich.live import Live
from rich.tree import Tree

STATE_ICONS = {"working": "●", "idle": "◌", "done": "✔", "blocked": "⏸"}


@dataclass(frozen=True)
class AgentStatus:
    role: str
    state: str
    current_task: str
    decision_count: int


def resolve_database_path(workdir: Path | None = None) -> Path:
    if configured_path := os.environ.get("WORKFLOW_DB"):
        return Path(configured_path).expanduser().resolve()
    return (workdir or Path.cwd()).expanduser().resolve() / "workflow.db"


def read_status(database_path: Path) -> list[AgentStatus]:
    if not database_path.is_file():
        return []

    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            rows = connection.execute(
                """
                SELECT status.role, status.state, status.current_task,
                       COUNT(decisions.id) AS decision_count
                FROM status
                LEFT JOIN decisions ON decisions.role = status.role
                GROUP BY status.role, status.state, status.current_task
                ORDER BY status.role
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [
        AgentStatus(
            role=str(row[0]),
            state=str(row[1]),
            current_task=str(row[2]),
            decision_count=int(row[3]),
        )
        for row in rows
    ]


def render_status(database_path: Path) -> Tree:
    tree = Tree(f"vibe-workflow · {database_path}")
    statuses = read_status(database_path)
    if not statuses:
        tree.add("No agent status yet")
        return tree

    for status in statuses:
        icon = STATE_ICONS.get(status.state, "?")
        tree.add(
            f"{icon} {status.role} · {status.current_task} "
            f"({status.decision_count} decisions)"
        )
    return tree


def _console() -> Console:
    output = sys.stdout
    if isinstance(output, TextIOWrapper) and output.encoding.lower() != "utf-8":
        output.reconfigure(encoding="utf-8", errors="replace")
    return Console(file=output)


def watch_status(database_path: Path, *, once: bool = False) -> None:
    console = _console()
    if once:
        console.print(render_status(database_path))
        return

    try:
        with Live(
            render_status(database_path), console=console, refresh_per_second=4
        ) as live:
            while True:
                live.update(render_status(database_path))
                time.sleep(1)
    except KeyboardInterrupt:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show vibe-workflow agent status")
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--once", action="store_true", help="Render one snapshot and exit"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    watch_status(resolve_database_path(args.workdir), once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
