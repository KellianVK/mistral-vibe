from __future__ import annotations

from pathlib import Path
import tomllib
from types import SimpleNamespace
from typing import cast

import pytest

from setup_workflow import (
    WORKFLOW_SKILL_MARKER,
    WORKFLOW_TOOL_MARKER,
    WorkflowConfigurationError,
    configure_workdir,
    render_mcp_config,
    workflow_database_path,
    workflow_skill_path,
    workflow_tool_path,
)
from vibe.core.config import AnyVibeConfig
import vibe.core.skills.manager as skill_manager_module
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillMetadata
from vibe.core.skills.parser import parse_skill_markdown
from vibe.core.tools.manager import ToolManager


def test_configure_workdir_preserves_existing_config_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    config_path = workdir / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    existing = (
        "# Keep this project configuration.\n"
        'active_model = "local"\n'
        "\n"
        "[[mcp_servers]]\n"
        'name = "existing"\n'
        'transport = "http"\n'
        'url = "http://127.0.0.1:9000"\n'
    )
    config_path.write_text(existing, encoding="utf-8")

    assert configure_workdir(workdir) == config_path
    first_render = config_path.read_text(encoding="utf-8")
    skill_path = workflow_skill_path(workdir)
    first_skill_render = skill_path.read_text(encoding="utf-8")
    tool_path = workflow_tool_path(workdir)
    first_tool_render = tool_path.read_text(encoding="utf-8")
    assert first_render.startswith(existing)

    assert configure_workdir(workdir) == config_path
    assert config_path.read_text(encoding="utf-8") == first_render
    assert skill_path.read_text(encoding="utf-8") == first_skill_render
    assert tool_path.read_text(encoding="utf-8") == first_tool_render

    parsed = tomllib.loads(first_render)
    assert parsed["active_model"] == "local"
    assert [server["name"] for server in parsed["mcp_servers"]] == [
        "existing",
        "workflow",
    ]
    workflow_server = parsed["mcp_servers"][1]
    assert workflow_server == {
        "name": "workflow",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "workflow_memory.server"],
        "env": {"WORKFLOW_DB": str(workflow_database_path(workdir))},
    }
    assert first_render.count('name = "workflow"') == 1

    frontmatter, body = parse_skill_markdown(first_skill_render)
    metadata = SkillMetadata.model_validate(frontmatter)
    assert metadata.name == "workflow"
    assert metadata.user_invocable is True
    assert metadata.allowed_tools == [
        "start_workflow",
        "get_workflow_status",
        "stop_workflow",
    ]
    assert "/workflow status" in body
    assert WORKFLOW_TOOL_MARKER in first_tool_render
    tool_classes = ToolManager._load_tools_from_file(tool_path)
    assert tool_classes is not None
    assert {tool_class.get_name() for tool_class in tool_classes} == {
        "start_workflow",
        "get_workflow_status",
        "stop_workflow",
    }

    harness = SimpleNamespace(project_skills_dirs=[], user_skills_dirs=[])
    monkeypatch.setattr(
        skill_manager_module, "get_harness_files_manager", lambda: harness
    )
    config = cast(
        AnyVibeConfig,
        SimpleNamespace(
            skill_paths=[skill_path.parent.parent],
            enabled_skills=[],
            disabled_skills=[],
        ),
    )
    manager = SkillManager(lambda: config)
    command = manager.parse_skill_command("/workflow status")
    assert command is not None
    assert command.name == "workflow"
    assert command.extra_instructions == "status"


@pytest.mark.parametrize("existing_kind", ["incompatible", "duplicate"])
def test_configure_workdir_rejects_incompatible_workflow_name_without_rewriting(
    existing_kind: str, tmp_path: Path
) -> None:
    workdir = tmp_path / existing_kind
    config_path = workdir / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    if existing_kind == "incompatible":
        existing = (
            "[[mcp_servers]]\n"
            'name = "workflow"\n'
            'transport = "http"\n'
            'url = "http://127.0.0.1:9000"\n'
        )
    else:
        exact_server = render_mcp_config(workflow_database_path(workdir))
        existing = f"{exact_server}\n{exact_server}"
    config_path.write_text(existing, encoding="utf-8")

    with pytest.raises(
        WorkflowConfigurationError, match="incompatible MCP server named 'workflow'"
    ):
        configure_workdir(workdir)

    assert config_path.read_text(encoding="utf-8") == existing


def test_configure_workdir_preserves_incompatible_workflow_skill(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    skill_path = workflow_skill_path(workdir)
    skill_path.parent.mkdir(parents=True)
    existing = "user-owned workflow skill\n"
    skill_path.write_text(existing, encoding="utf-8")

    with pytest.raises(
        WorkflowConfigurationError, match="incompatible /workflow skill"
    ):
        configure_workdir(workdir)

    assert skill_path.read_text(encoding="utf-8") == existing
    assert not (workdir / ".vibe" / "config.toml").exists()


def test_configure_workdir_updates_managed_workflow_skill(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    skill_path = workflow_skill_path(workdir)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        f"old managed content\n{WORKFLOW_SKILL_MARKER}\n", encoding="utf-8"
    )

    configure_workdir(workdir)

    updated = skill_path.read_text(encoding="utf-8")
    assert updated != f"old managed content\n{WORKFLOW_SKILL_MARKER}\n"
    assert WORKFLOW_SKILL_MARKER in updated


def test_configure_workdir_preserves_incompatible_workflow_tool(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    tool_path = workflow_tool_path(workdir)
    tool_path.parent.mkdir(parents=True)
    existing = "user-owned workflow tool\n"
    tool_path.write_text(existing, encoding="utf-8")

    with pytest.raises(
        WorkflowConfigurationError, match="incompatible workflow control tool"
    ):
        configure_workdir(workdir)

    assert tool_path.read_text(encoding="utf-8") == existing
    assert not (workdir / ".vibe" / "config.toml").exists()


def test_configure_workdir_updates_managed_workflow_tool(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    tool_path = workflow_tool_path(workdir)
    tool_path.parent.mkdir(parents=True)
    tool_path.write_text(
        f"old managed content\n{WORKFLOW_TOOL_MARKER}\n", encoding="utf-8"
    )

    configure_workdir(workdir)

    updated = tool_path.read_text(encoding="utf-8")
    assert updated != f"old managed content\n{WORKFLOW_TOOL_MARKER}\n"
    assert WORKFLOW_TOOL_MARKER in updated
