from __future__ import annotations

from pathlib import Path

import pytest

from vibe.workflow.roles import select_roles
from vibe.workflow.setup import (
    WorkflowConfigurationError,
    agent_profile_path,
    configure_workdir,
    workflow_database_path,
)


def test_configure_workdir_provisions_agent_profiles(tmp_path: Path) -> None:
    roles = select_roles(None)
    configure_workdir(tmp_path, roles)

    for role in roles:
        profile = agent_profile_path(tmp_path, role)
        assert profile.is_file()
        content = profile.read_text(encoding="utf-8")
        assert f'display_name = "{role.name}"' in content
        assert f'active_model = "{role.model}"' in content
        assert "bypass_tool_permissions = true" in content


def test_configure_workdir_is_idempotent(tmp_path: Path) -> None:
    roles = select_roles(None)
    configure_workdir(tmp_path, roles)
    before = agent_profile_path(tmp_path, roles[0]).read_text(encoding="utf-8")

    configure_workdir(tmp_path, roles)

    assert agent_profile_path(tmp_path, roles[0]).read_text(encoding="utf-8") == before


def test_configure_workdir_refuses_to_overwrite_unmanaged_profile(
    tmp_path: Path,
) -> None:
    roles = select_roles(["Planner"])
    profile = agent_profile_path(tmp_path, roles[0])
    profile.parent.mkdir(parents=True)
    profile.write_text('display_name = "My custom planner"\n', encoding="utf-8")

    with pytest.raises(WorkflowConfigurationError, match="unmanaged agent profile"):
        configure_workdir(tmp_path, roles)


def test_configure_workdir_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkflowConfigurationError, match="does not exist"):
        configure_workdir(tmp_path / "missing", select_roles(None))


def test_database_lives_inside_vibe_dir(tmp_path: Path) -> None:
    assert workflow_database_path(tmp_path) == tmp_path / ".vibe" / "workflow.db"
