from __future__ import annotations

import pytest

from vibe.workflow import roles


def test_default_selection_is_the_full_team() -> None:
    selected = roles.select_roles(None)
    assert [role.name for role in selected] == [
        "Planner",
        "Backend",
        "Frontend",
        "QA",
        "Security",
        "DevOps",
        "Docs",
        "Reviewer",
    ]


def test_selection_is_case_insensitive_and_rejects_unknown() -> None:
    selected = roles.select_roles(["planner", "QA"])
    assert [role.name for role in selected] == ["Planner", "QA"]

    with pytest.raises(roles.RoleSelectionError, match="Unknown role"):
        roles.select_roles(["Planner", "Designer"])


def test_dependencies_on_excluded_roles_are_dropped() -> None:
    (qa,) = roles.select_roles(["QA"])
    assert qa.depends_on == ()

    selected = roles.select_roles(["Backend", "QA"])
    qa = next(role for role in selected if role.name == "QA")
    assert qa.depends_on == ("Backend",)


def test_execution_waves_follow_dependency_levels() -> None:
    selected = roles.select_roles(["Planner", "Backend", "Frontend", "QA"])
    waves = roles.execution_waves(selected)
    assert [[role.name for role in wave] for wave in waves] == [
        ["Planner"],
        ["Backend", "Frontend"],
        ["QA"],
    ]


def test_execution_waves_detect_cycles() -> None:
    a = roles.RoleSpec(
        name="A", agent_profile="a", model="m", objective="o", depends_on=("B",)
    )
    b = roles.RoleSpec(
        name="B", agent_profile="b", model="m", objective="o", depends_on=("A",)
    )
    with pytest.raises(roles.RoleSelectionError, match="Circular"):
        roles.execution_waves([a, b])


def test_full_team_composition_rules(tmp_path):
    from vibe.workflow.init_flow import compose_team
    from vibe.workflow.scanner import ProjectScan

    minimal = compose_team(wants_frontend=False, wants_devops=False)
    assert minimal == ["Planner", "Reviewer", "Backend", "QA", "Security", "Docs"]
    assert len(minimal) == 6

    full = compose_team(wants_frontend=True, wants_devops=True)
    assert "Frontend" in full and "DevOps" in full
    assert len(full) == 8

    scan = ProjectScan(file_count=10, has_frontend=True, has_ci=True)
    from_scan = compose_team(wants_frontend=False, wants_devops=False, scan=scan)
    assert "Frontend" in from_scan and "DevOps" in from_scan


def test_full_team_waves_are_consistent():
    selected = roles.select_roles([
        "Planner",
        "Reviewer",
        "Backend",
        "Frontend",
        "QA",
        "Security",
        "Docs",
        "DevOps",
    ])
    waves = roles.execution_waves(selected)
    names = [[r.name for r in wave] for wave in waves]
    assert names[0] == ["Planner"]
    assert set(names[1]) == {"Backend", "Frontend"}
    assert set(names[2]) == {"QA", "Security", "Docs", "DevOps"}
    assert names[3] == ["Reviewer"]
