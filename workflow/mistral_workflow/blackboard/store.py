import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_EMPTY_STATE: dict[str, Any] = {"agents": {}, "decisions": [], "questions": []}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Blackboard:
    """Shared state file for a workflow run. JSON on disk, atomic writes
    (temp file + os.replace) so readers never see a partial write, plus an
    in-process lock so concurrent roles in the same `mistral workflow run`
    don't interleave writes. Simplification assumed for the MVP: no
    cross-process lock — only one `run` is expected to write at a time,
    while blackboard/api.py only reads.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(dict(_EMPTY_STATE))

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(_EMPTY_STATE)
        with self.path.open() as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.path)

    def update_status(self, role: str, status: str, current_task: str | None = None) -> None:
        with self._lock:
            data = self._read()
            data["agents"][role] = {
                "status": status,
                "current_task": current_task,
                "updated_at": _now(),
            }
            self._write(data)

    def publish_decision(self, role: str, summary: str, artifact: str | None = None) -> None:
        with self._lock:
            data = self._read()
            data["decisions"].append(
                {"role": role, "summary": summary, "artifact": artifact, "ts": _now()}
            )
            self._write(data)

    def read_decisions(self, role: str | None = None) -> list[dict[str, Any]]:
        decisions = self._read()["decisions"]
        if role:
            decisions = [d for d in decisions if d["role"] == role]
        return decisions

    def request_review(self, from_role: str, to_role: str, question: str) -> None:
        with self._lock:
            data = self._read()
            data["questions"].append(
                {
                    "from": from_role,
                    "to": to_role,
                    "question": question,
                    "resolved": False,
                    "ts": _now(),
                }
            )
            self._write(data)

    def resolve_question(self, index: int) -> None:
        with self._lock:
            data = self._read()
            data["questions"][index]["resolved"] = True
            self._write(data)

    def state(self) -> dict[str, Any]:
        return self._read()

    def reset(self) -> None:
        with self._lock:
            self._write(dict(_EMPTY_STATE))


def blackboard_path(project_dir: Path) -> Path:
    return project_dir / ".vibe" / "workflow-state.json"
