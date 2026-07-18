"""Provision a target workdir for a MiaouFlow run.

Writes one Vibe agent profile per role into ``.vibe/agents/`` so each spawned
worker gets its pinned model and permission bypass. Files carry a managed-by
marker: unmanaged files with the same name are never overwritten.
"""

from __future__ import annotations

from pathlib import Path

from vibe.workflow.roles import RoleSpec

MANAGED_MARKER = "# Managed by vibe workflow (MiaouFlow)."


class WorkflowConfigurationError(RuntimeError):
    pass


def workflow_database_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".vibe" / "workflow.db"


def agent_profile_path(workdir: Path, role: RoleSpec) -> Path:
    return (
        workdir.expanduser().resolve()
        / ".vibe"
        / "agents"
        / f"{role.agent_profile}.toml"
    )


def render_agent_profile(role: RoleSpec) -> str:
    return (
        f"{MANAGED_MARKER}\n"
        f'display_name = "{role.name}"\n'
        f'description = "MiaouFlow {role.name} agent"\n'
        f'active_model = "{role.model}"\n'
        "bypass_tool_permissions = true\n"
    )


def _validate_managed_file(path: Path, content: str) -> None:
    if not path.exists():
        return
    existing = path.read_text(encoding="utf-8")
    if existing != content and MANAGED_MARKER not in existing:
        raise WorkflowConfigurationError(
            f"An unmanaged agent profile already exists: {path}"
        )


def _write_managed_file(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def configure_workdir(workdir: Path, roles: list[RoleSpec]) -> Path:
    resolved_workdir = workdir.expanduser().resolve()
    if not resolved_workdir.is_dir():
        raise WorkflowConfigurationError(
            f"Workdir does not exist or is not a directory: {resolved_workdir}"
        )

    vibe_dir = resolved_workdir / ".vibe"
    vibe_dir.mkdir(parents=True, exist_ok=True)

    profiles = [
        (agent_profile_path(resolved_workdir, role), render_agent_profile(role))
        for role in roles
    ]
    for path, content in profiles:
        _validate_managed_file(path, content)
    for path, content in profiles:
        _write_managed_file(path, content)
    return vibe_dir
