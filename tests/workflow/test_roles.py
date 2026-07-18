from __future__ import annotations

import pytest

from vibe.workflow import roles


def test_default_selection_is_planner_backend_frontend() -> None:
    selected = roles.select_roles(None)
    assert [role.name for role in selected] == ["Planner", "Backend", "Frontend"]


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
