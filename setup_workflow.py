from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from importlib import resources
import json
from pathlib import Path
import tomllib
from typing import Any


class WorkflowConfigurationError(RuntimeError):
    pass


MEMORY_SERVER_NAME = "workflow"
WORKFLOW_SKILL_NAME = "workflow"
WORKFLOW_SKILL_MARKER = "<!-- Managed by vibe-workflow. -->"
WORKFLOW_TOOL_MARKER = "# Managed by vibe-workflow."


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


def _matches_memory_server(server: dict[str, Any], database_path: Path) -> bool:
    env = server.get("env")
    return (
        server.get("transport") == "stdio"
        and server.get("command") == "python"
        and server.get("args") == ["-m", "workflow_memory.server"]
        and isinstance(env, dict)
        and env.get("WORKFLOW_DB") == str(database_path)
    )


def _has_compatible_server(
    servers: list[dict[str, Any]],
    name: str,
    matches: Callable[[dict[str, Any]], bool],
    config_path: Path,
) -> bool:
    named_servers = [server for server in servers if server.get("name") == name]
    if not named_servers:
        return False
    if len(named_servers) == 1 and matches(named_servers[0]):
        return True
    raise WorkflowConfigurationError(
        f"An incompatible MCP server named {name!r} already exists in {config_path}"
    )


def workflow_skill_path(workdir: Path) -> Path:
    return (
        workdir.expanduser().resolve()
        / ".vibe"
        / "skills"
        / WORKFLOW_SKILL_NAME
        / "SKILL.md"
    )


def workflow_tool_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".vibe" / "tools" / "workflow_control.py"


def _workflow_skill_content() -> str:
    return (
        resources
        .files("workflow_control")
        .joinpath("workflow_skill.md")
        .read_text(encoding="utf-8")
    )


def _workflow_tool_content() -> str:
    return (
        resources
        .files("workflow_control")
        .joinpath("workflow_tool.py")
        .read_text(encoding="utf-8")
    )


def _validate_managed_file(
    path: Path, content: str, marker: str, conflict: str
) -> None:
    if not path.exists():
        return
    existing = path.read_text(encoding="utf-8")
    if existing != content and marker not in existing:
        raise WorkflowConfigurationError(f"{conflict}: {path}")


def _write_managed_file(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
    managed_files = [
        (
            workflow_skill_path(resolved_workdir),
            _workflow_skill_content(),
            WORKFLOW_SKILL_MARKER,
            f"An incompatible /{WORKFLOW_SKILL_NAME} skill already exists",
        ),
        (
            workflow_tool_path(resolved_workdir),
            _workflow_tool_content(),
            WORKFLOW_TOOL_MARKER,
            "An incompatible workflow control tool already exists",
        ),
    ]

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    try:
        parsed = tomllib.loads(existing) if existing.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise WorkflowConfigurationError(
            f"Cannot update invalid TOML configuration: {config_path}"
        ) from error

    servers = _workflow_servers(parsed)
    memory_configured = _has_compatible_server(
        servers,
        MEMORY_SERVER_NAME,
        lambda server: _matches_memory_server(server, database_path),
        config_path,
    )
    for managed_file in managed_files:
        _validate_managed_file(*managed_file)

    blocks: list[str] = []
    if not memory_configured:
        blocks.append(render_mcp_config(database_path))
    if blocks:
        separator = "" if not existing or existing.endswith("\n\n") else "\n"
        rendered_blocks = "\n".join(blocks)
        config_path.write_text(
            f"{existing}{separator}{rendered_blocks}", encoding="utf-8"
        )

    for path, content, _marker, _conflict in managed_files:
        _write_managed_file(path, content)
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
    print(
        f"Configured workflow MCP, control tools, and /workflow skill in {config_path}"
    )
    print("Trust this folder once in interactive Vibe before starting the workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
