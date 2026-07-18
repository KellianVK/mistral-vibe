"""Role manifest and wave scheduling for MiaouFlow teams."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleSpec:
    name: str
    agent_profile: str
    model: str
    objective: str
    depends_on: tuple[str, ...] = field(default=())
    max_turns: int = 40


PLANNER_MODEL = "mistral-medium-3.5"
CODER_MODEL = "devstral-small"

DEFAULT_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        name="Planner",
        agent_profile="planner",
        model=PLANNER_MODEL,
        objective=(
            "Decompose the goal into a concrete plan with file ownership and "
            "interface contracts, published to the blackboard."
        ),
        max_turns=15,
    ),
    RoleSpec(
        name="Backend",
        agent_profile="backend",
        model=CODER_MODEL,
        objective=(
            "Implement server-side features. Publish every API contract to the "
            "blackboard BEFORE implementing it."
        ),
        depends_on=("Planner",),
    ),
    RoleSpec(
        name="Frontend",
        agent_profile="frontend",
        model=CODER_MODEL,
        objective=(
            "Implement the UI against the contracts Backend publishes; ask "
            "Backend via the blackboard instead of guessing."
        ),
        depends_on=("Planner",),
    ),
    RoleSpec(
        name="QA",
        agent_profile="qa",
        model=CODER_MODEL,
        objective=(
            "Independently test what Backend and Frontend built and publish a verdict."
        ),
        depends_on=("Planner", "Backend", "Frontend"),
    ),
)

DEFAULT_ACTIVE_ROLES: tuple[str, ...] = ("Planner", "Backend", "Frontend")


class RoleSelectionError(ValueError):
    pass


def select_roles(names: list[str] | None = None) -> list[RoleSpec]:
    """Resolve requested role names (case-insensitive) against the manifest.

    Dependencies on excluded roles are dropped so any subset stays runnable.
    """
    wanted = [name for name in (names or list(DEFAULT_ACTIVE_ROLES))]
    by_lower = {role.name.lower(): role for role in DEFAULT_ROLES}
    selected: list[RoleSpec] = []
    seen: set[str] = set()
    for name in wanted:
        role = by_lower.get(name.strip().lower())
        if role is None:
            known = ", ".join(role.name for role in DEFAULT_ROLES)
            raise RoleSelectionError(f"Unknown role {name!r}; known roles: {known}")
        if role.name not in seen:
            seen.add(role.name)
            selected.append(role)

    active_names = {role.name for role in selected}
    return [
        RoleSpec(
            name=role.name,
            agent_profile=role.agent_profile,
            model=role.model,
            objective=role.objective,
            depends_on=tuple(dep for dep in role.depends_on if dep in active_names),
            max_turns=role.max_turns,
        )
        for role in selected
    ]


def execution_waves(roles: list[RoleSpec]) -> list[list[RoleSpec]]:
    """Kahn's algorithm collapsed into dependency levels.

    Every role in a wave has all its dependencies satisfied by earlier waves,
    so each wave can run fully in parallel.
    """
    placed: set[str] = set()
    remaining = list(roles)
    waves: list[list[RoleSpec]] = []
    while remaining:
        ready = [role for role in remaining if set(role.depends_on) <= placed]
        if not ready:
            stuck = sorted(role.name for role in remaining)
            raise RoleSelectionError(f"Circular depends_on among role(s): {stuck}")
        waves.append(ready)
        placed.update(role.name for role in ready)
        remaining = [role for role in remaining if role.name not in placed]
    return waves


def manifest_payload(roles: list[RoleSpec]) -> list[dict[str, object]]:
    """Wire shape for the board's /state and /manifest endpoints."""
    return [
        {
            "name": role.name,
            "agent_profile": role.agent_profile,
            "model": role.model,
            "objective": role.objective,
            "depends_on": list(role.depends_on),
        }
        for role in roles
    ]
