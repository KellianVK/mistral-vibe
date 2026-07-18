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


def test_scanner_detects_project_shape(tmp_path: Path) -> None:
    from vibe.workflow.scanner import scan_project

    empty = scan_project(tmp_path)
    assert empty.is_empty

    (tmp_path / "app.py").write_text("import flask\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask\npytest\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html>", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test(): pass", encoding="utf-8"
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")

    scan = scan_project(tmp_path)
    assert not scan.is_empty
    assert "Python" in scan.languages
    assert "flask" in scan.frameworks
    assert scan.has_frontend and scan.has_tests and scan.has_ci and scan.has_docs
    assert "Python" in scan.summary()


def test_init_noninteractive_with_goal_provisions_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from vibe.workflow.init_flow import load_team_config, run_init

    # Non-empty project with frontend + CI -> scan-driven team, no questions
    # except confirmations, which we answer via stdin monkeypatching.
    (tmp_path / "app.py").write_text("import flask\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html>", encoding="utf-8")
    answers = iter(["", "n"])  # accept proposed team; standard quality
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    exit_code = run_init(
        tmp_path, auto_run=False, board_port=8787, goal_override="Ship v1"
    )

    assert exit_code == 0
    config = load_team_config(tmp_path)
    assert config is not None
    assert config["goal_default"] == "Ship v1"
    roles = config["roles"]
    assert isinstance(roles, list) and "Frontend" in roles and "Reviewer" in roles
    # Agent profiles were provisioned for the whole team.
    profiles = {p.stem for p in (tmp_path / ".vibe" / "agents").glob("*.toml")}
    assert {"planner", "reviewer", "frontend", "security"} <= profiles
    out = capsys.readouterr().out
    assert "Team of" in out


def test_generated_profiles_restrict_tools_per_role(tmp_path: Path) -> None:
    roles = select_roles(["Planner", "Reviewer", "Security", "Backend"])
    configure_workdir(tmp_path, roles)

    planner = agent_profile_path(tmp_path, roles[0]).read_text(encoding="utf-8")
    assert 'disabled_tools = ["write_file", "edit", "bash"]' in planner
    reviewer = (tmp_path / ".vibe" / "agents" / "reviewer.toml").read_text(
        encoding="utf-8"
    )
    assert 'disabled_tools = ["write_file", "edit", "bash"]' in reviewer
    security = (tmp_path / ".vibe" / "agents" / "security.toml").read_text(
        encoding="utf-8"
    )
    assert 'disabled_tools = ["write_file", "edit"]' in security
    backend = (tmp_path / ".vibe" / "agents" / "backend.toml").read_text(
        encoding="utf-8"
    )
    assert "disabled_tools" not in backend


def test_hooks_config_written_only_with_reviewer(tmp_path: Path) -> None:
    from vibe.workflow.setup import hooks_config_path

    no_reviewer = tmp_path / "a"
    no_reviewer.mkdir()
    configure_workdir(no_reviewer, select_roles(["Planner", "Backend"]))
    assert not hooks_config_path(no_reviewer).exists()

    with_reviewer = tmp_path / "b"
    with_reviewer.mkdir()
    configure_workdir(with_reviewer, select_roles(["Planner", "Backend", "Reviewer"]))
    content = hooks_config_path(with_reviewer).read_text(encoding="utf-8")
    assert "miaouflow-reviewer-gate" in content
    assert 'type = "pre_tool"' in content
    assert "guard_push" in content
