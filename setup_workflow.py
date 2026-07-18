from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import tomllib
from typing import Any


class WorkflowConfigurationError(RuntimeError):
    pass


def workflow_database_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / "workflow.db"


def render_mcp_config(database_path: Path) -> str:
    encoded_path = json.dumps(str(database_path.expanduser().resolve()))
    return (
        "# Added by vibe-workflow.\n"
        "[[mcp_servers]]\n"
        'name = "workflow"\n'
        'transport = "stdio"\n'
        'command = "python"\n'
        'args = ["-m", "workflow_memory.server"]\n'
        f'env = {{ "WORKFLOW_DB" = {encoded_path} }}\n'
    )


def _workflow_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config.get("mcp_servers", [])
    if not isinstance(servers, list):
        raise WorkflowConfigurationError("mcp_servers must be a TOML array of tables")
    return [server for server in servers if isinstance(server, dict)]


def _matches(server: dict[str, Any], database_path: Path) -> bool:
    env = server.get("env")
    return (
        server.get("transport") == "stdio"
        and server.get("command") == "python"
        and server.get("args") == ["-m", "workflow_memory.server"]
        and isinstance(env, dict)
        and env.get("WORKFLOW_DB") == str(database_path)
    )


def configure_workdir(workdir: Path) -> Path:
    resolved_workdir = workdir.expanduser().resolve()
    if not resolved_workdir.is_dir():
        raise WorkflowConfigurationError(
            f"Workdir does not exist or is not a directory: {resolved_workdir}"
        )

    vibe_dir = resolved_workdir / ".vibe"
    vibe_dir.mkdir(parents=True, exist_ok=True)
    config_path = vibe_dir / "config.toml"
    database_path = workflow_database_path(resolved_workdir)

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    try:
        parsed = tomllib.loads(existing) if existing.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise WorkflowConfigurationError(
            f"Cannot update invalid TOML configuration: {config_path}"
        ) from error

    workflow_servers = [
        server
        for server in _workflow_servers(parsed)
        if server.get("name") == "workflow"
    ]
    if workflow_servers:
        if len(workflow_servers) == 1 and _matches(workflow_servers[0], database_path):
            return config_path
        raise WorkflowConfigurationError(
            f"An incompatible MCP server named 'workflow' already exists in {config_path}"
        )

    separator = "" if not existing or existing.endswith("\n\n") else "\n"
    config_path.write_text(
        f"{existing}{separator}{render_mcp_config(database_path)}", encoding="utf-8"
    )
    return config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure a target project for vibe-workflow"
    )
    parser.add_argument(
        "--workdir", type=Path, required=True, help="Target project directory"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config_path = configure_workdir(args.workdir)
    except WorkflowConfigurationError as error:
        print(f"error: {error}")
        return 1
    print(f"Configured workflow MCP server in {config_path}")
    print("Trust this folder once in interactive Vibe before starting the workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
