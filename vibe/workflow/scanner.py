"""Project scanner: detect what an existing project contains.

Drives team composition for `vibe workflow init` on a non-empty directory —
frontend code enables the Frontend role, CI enables DevOps, and the summary
is echoed back for confirmation instead of re-asking about the stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

_LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
}
_FRONTEND_MARKERS = {".html", ".css", ".tsx", ".jsx", ".vue", ".svelte"}
_SKIPPED_DIRS = {
    ".git",
    ".vibe",
    "node_modules",
    "__pycache__",
    ".venv",
    "logs",
    "dist",
    "build",
}
_MAX_FILES = 400


@dataclass
class ProjectScan:
    file_count: int = 0
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    has_frontend: bool = False
    has_tests: bool = False
    has_ci: bool = False
    has_docs: bool = False

    @property
    def is_empty(self) -> bool:
        return self.file_count == 0

    def summary(self) -> str:
        if self.is_empty:
            return "Empty project."
        parts = [f"{self.file_count} files"]
        if self.languages:
            parts.append("languages: " + ", ".join(self.languages))
        if self.frameworks:
            parts.append("frameworks: " + ", ".join(self.frameworks))
        flags = [
            label
            for flag, label in (
                (self.has_frontend, "frontend code"),
                (self.has_tests, "tests"),
                (self.has_ci, "CI"),
                (self.has_docs, "docs"),
            )
            if flag
        ]
        if flags:
            parts.append("has " + ", ".join(flags))
        return "; ".join(parts) + "."


def _detect_frameworks(workdir: Path) -> list[str]:
    frameworks: list[str] = []
    requirements = workdir / "requirements.txt"
    if requirements.is_file():
        content = requirements.read_text(encoding="utf-8", errors="replace").lower()
        for name in ("flask", "fastapi", "django", "starlette"):
            if name in content:
                frameworks.append(name)
    package_json = workdir / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except (json.JSONDecodeError, AttributeError):
            deps = {}
        for name in ("react", "vue", "svelte", "next", "express"):
            if name in deps:
                frameworks.append(name)
    if (workdir / "pyproject.toml").is_file():
        frameworks.append("pyproject")
    return frameworks


def scan_project(workdir: Path) -> ProjectScan:
    scan = ProjectScan()
    languages: set[str] = set()
    for current_root, dirnames, filenames in Path(workdir).walk():
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS]
        relative_root = current_root.relative_to(workdir)
        root_parts = {part.lower() for part in relative_root.parts}
        for filename in filenames:
            if filename.startswith("."):
                continue
            scan.file_count += 1
            suffix = Path(filename).suffix.lower()
            lower = filename.lower()
            if suffix in _LANGUAGE_EXTENSIONS:
                languages.add(_LANGUAGE_EXTENSIONS[suffix])
            if suffix in _FRONTEND_MARKERS:
                scan.has_frontend = True
            if (
                lower.startswith("test_")
                or lower.endswith("_test.py")
                or "tests" in root_parts
            ):
                scan.has_tests = True
            if lower.startswith("readme") or "docs" in root_parts:
                scan.has_docs = True
            if scan.file_count >= _MAX_FILES:
                break
        if "workflows" in root_parts and ".github" in {
            p.lower() for p in relative_root.parts
        }:
            scan.has_ci = True
        if scan.file_count >= _MAX_FILES:
            break
    if (workdir / ".github" / "workflows").is_dir():
        scan.has_ci = True
    scan.languages = sorted(languages)
    scan.frameworks = _detect_frameworks(workdir)
    return scan
