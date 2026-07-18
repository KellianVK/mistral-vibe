import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VIBE_BIN = shutil.which("vibe") or "vibe"
DEFAULT_TIMEOUT_SEC = 900
DEFAULT_MAX_TURNS = 20


@dataclass
class VibeResult:
    success: bool
    final_text: str
    messages: list[dict] = field(default_factory=list)
    error: str | None = None
    returncode: int | None = None


def run_vibe_role(
    *,
    prompt: str,
    agent: str,
    workdir: Path,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> VibeResult:
    """Drive one headless Vibe turn as a given agent profile against workdir.

    Uses `vibe -p` (see NOTES-FORK.md Q1): --trust is required or project-level
    .vibe/agents/ silently fails to load; --auto-approve is required or ASK-gated
    tool calls are silently skipped rather than blocked.
    """
    cmd = [
        VIBE_BIN,
        "-p",
        prompt,
        "--agent",
        agent,
        "--trust",
        "--auto-approve",
        "--output",
        "json",
        "--workdir",
        str(workdir),
        "--max-turns",
        str(max_turns),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return VibeResult(
            success=False,
            final_text="",
            error=f"vibe timed out after {timeout_sec}s (agent={agent})",
        )
    except FileNotFoundError:
        return VibeResult(
            success=False,
            final_text="",
            error=f"'{VIBE_BIN}' not found on PATH — is mistral-vibe installed?",
        )

    if proc.returncode != 0:
        return VibeResult(
            success=False,
            final_text="",
            error=(proc.stderr or proc.stdout)[-4000:],
            returncode=proc.returncode,
        )

    try:
        messages = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return VibeResult(
            success=False,
            final_text=proc.stdout[-4000:],
            error=f"could not parse vibe --output json: {e}",
            returncode=proc.returncode,
        )

    final_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls"):
            final_text = msg["content"]
            break

    return VibeResult(success=True, final_text=final_text, messages=messages, returncode=0)
