import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class RoleSpec(BaseModel):
    name: str
    agent_profile: str
    model: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class ProjectSpec(BaseModel):
    goal: str
    gates: list[str] = Field(default_factory=list)
    max_loop_iterations: int = 3


class WorkflowManifest(BaseModel):
    project: ProjectSpec
    roles: list[RoleSpec]

    @model_validator(mode="after")
    def _check_depends_on_reference_known_roles(self) -> "WorkflowManifest":
        names = {r.name for r in self.roles}
        for role in self.roles:
            unknown = set(role.depends_on) - names
            if unknown:
                raise ValueError(f"role '{role.name}' depends_on unknown role(s): {sorted(unknown)}")
        return self

    def role(self, name: str) -> RoleSpec:
        for r in self.roles:
            if r.name == name:
                return r
        raise KeyError(f"no such role: {name}")

    def execution_order(self) -> list[RoleSpec]:
        """Topological order (Kahn's algorithm). Ties broken by declaration order."""
        by_name = {r.name: r for r in self.roles}
        remaining_deps = {r.name: set(r.depends_on) for r in self.roles}
        ordered: list[RoleSpec] = []
        placed: set[str] = set()

        while len(ordered) < len(self.roles):
            ready = [
                r for r in self.roles
                if r.name not in placed and remaining_deps[r.name] <= placed
            ]
            if not ready:
                stuck = set(by_name) - placed
                raise ValueError(f"circular depends_on among role(s): {sorted(stuck)}")
            for r in ready:
                ordered.append(r)
                placed.add(r.name)
        return ordered


def load_manifest(path: Path) -> WorkflowManifest:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return WorkflowManifest.model_validate(data)


def manifest_path(project_dir: Path) -> Path:
    return project_dir / ".vibe" / "workflow.toml"
