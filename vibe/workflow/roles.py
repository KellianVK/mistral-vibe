"""Role manifest and wave scheduling for MiaouFlow teams."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


@dataclass(frozen=True)
class RoleSpec:
    name: str
    agent_profile: str
    model: str
    objective: str
    depends_on: tuple[str, ...] = field(default=())
    max_turns: int = 40
    disabled_tools: tuple[str, ...] = field(default=())


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
        disabled_tools=("write_file", "edit", "bash"),
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
        max_turns=60,
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
        max_turns=60,
    ),
    RoleSpec(
        name="QA",
        agent_profile="qa",
        model=CODER_MODEL,
        objective=(
            "Independently test what Backend and Frontend built and publish a verdict."
        ),
        depends_on=("Planner", "Backend", "Frontend"),
        max_turns=50,
    ),
    RoleSpec(
        name="Security",
        agent_profile="security",
        model=CODER_MODEL,
        objective=(
            "Audit the implementation for security flaws: injection, missing "
            "input validation, weak secrets/JWT handling, auth bypasses. Do not "
            "rewrite code — send_message each finding to the owning role and "
            "publish one security-verdict decision summarizing the audit."
        ),
        depends_on=("Planner", "Backend"),
        max_turns=25,
        disabled_tools=("write_file", "edit"),
    ),
    RoleSpec(
        name="DevOps",
        agent_profile="devops",
        model=CODER_MODEL,
        objective=(
            "Prepare the run/deploy story from what the team built: a run "
            "command, dependency check, and a minimal CI config if the project "
            "has none. Do not change application logic."
        ),
        depends_on=("Planner", "Backend"),
        max_turns=25,
    ),
    RoleSpec(
        name="Docs",
        agent_profile="docs",
        model=CODER_MODEL,
        objective=(
            "Write or update the project README from the published decisions "
            "and delivered files: what it is, how to run it, the API surface. "
            "Do not change application code."
        ),
        depends_on=("Planner", "Backend", "Frontend"),
        max_turns=20,
    ),
    RoleSpec(
        name="Reviewer",
        agent_profile="reviewer",
        model=PLANNER_MODEL,
        objective=(
            "Review the delivered code and every published verdict (QA, "
            "Security) for correctness and quality. Do not rewrite code. "
            "Publish one final decision with topic review-verdict whose "
            "summary starts with exactly GO: or NO-GO: and the reasons."
        ),
        depends_on=("QA", "Security", "Docs"),
        max_turns=20,
        disabled_tools=("write_file", "edit", "bash"),
    ),
)

DEFAULT_ACTIVE_ROLES: tuple[str, ...] = (
    "Planner",
    "Backend",
    "Frontend",
    "QA",
    "Security",
    "DevOps",
    "Docs",
    "Reviewer",
)

FULL_TEAM_BASE: tuple[str, ...] = (
    "Planner",
    "Reviewer",
    "Backend",
    "QA",
    "Security",
    "Docs",
)


class RoleSelectionError(ValueError):
    pass


def custom_roles_path(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".vibe" / "custom_roles.json"


def load_custom_roles(workdir: Path | None) -> list[RoleSpec]:
    """User-added roles (board's Add-agent tab); they join the next run."""
    if workdir is None:
        return []
    path = custom_roles_path(workdir)
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    roles: list[RoleSpec] = []
    if not isinstance(entries, list):
        return []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"]).strip()
        roles.append(
            RoleSpec(
                name=name,
                agent_profile=str(entry.get("agent_profile") or name.lower()),
                model=str(entry.get("model") or CODER_MODEL),
                objective=str(entry.get("objective") or f"Act as the {name} agent."),
                depends_on=tuple(entry.get("depends_on") or ("Planner",)),
                max_turns=int(entry.get("max_turns") or 40),
            )
        )
    return roles


def save_custom_role(workdir: Path, role: RoleSpec) -> None:
    path = custom_roles_path(workdir)
    existing = [
        entry
        for entry in load_custom_roles(workdir)
        if entry.name.lower() != role.name.lower()
    ]
    entries = [
        {
            "name": entry.name,
            "agent_profile": entry.agent_profile,
            "model": entry.model,
            "objective": entry.objective,
            "depends_on": list(entry.depends_on),
            "max_turns": entry.max_turns,
        }
        for entry in [*existing, role]
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def select_roles(
    names: list[str] | None = None, workdir: Path | None = None
) -> list[RoleSpec]:
    """Resolve requested role names (case-insensitive) against the manifest.

    Custom roles registered in the workdir join the default active set.
    Dependencies on excluded roles are dropped so any subset stays runnable.
    """
    custom = load_custom_roles(workdir)
    manifest = [*DEFAULT_ROLES, *custom]
    if names:
        wanted = list(names)
    else:
        wanted = [*DEFAULT_ACTIVE_ROLES, *[role.name for role in custom]]
    by_lower = {role.name.lower(): role for role in manifest}
    selected: list[RoleSpec] = []
    seen: set[str] = set()
    for name in wanted:
        role = by_lower.get(name.strip().lower())
        if role is None:
            known = ", ".join(role.name for role in manifest)
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
            disabled_tools=role.disabled_tools,
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
