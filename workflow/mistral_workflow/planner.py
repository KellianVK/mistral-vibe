import json
from datetime import UTC, datetime
from pathlib import Path

from mistral_workflow.blackboard.store import Blackboard
from mistral_workflow.roles import RoleSpec, WorkflowManifest
from mistral_workflow.vibe_runner import VibeResult, run_vibe_role


def role_log_path(project_dir: Path, role_name: str) -> Path:
    return project_dir / ".vibe" / "logs" / f"{role_name}.json"


def _write_role_log(project_dir: Path, role_name: str, result: VibeResult) -> None:
    path = role_log_path(project_dir, role_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "role": role_name,
                "success": result.success,
                "final_text": result.final_text,
                "error": result.error,
                "messages": result.messages,
                "ts": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )

ROLE_TASK_FRAMING = {
    "planner": (
        "Break the project goal down into a short, concrete implementation plan for the "
        "team (Backend, QA, Reviewer). Do not write code yourself. List the concrete "
        "files/endpoints/functions to build and the order to build them in. Keep it to a "
        "handful of bullet points."
    ),
    "backend": (
        "Implement the current step of the plan in this project's codebase. Write real, "
        "working code and create whatever files are needed. If the log below includes a "
        "QA failure, fix that specific failure rather than starting over."
    ),
    "qa": (
        "Write and/or run tests for the code Backend just produced. Actually execute the "
        "test suite with a shell command rather than just reading the code. Your final "
        "message MUST start with a line that is exactly 'RESULT: PASS' or 'RESULT: FAIL', "
        "followed by a short explanation of what you tested."
    ),
    "reviewer": (
        "Review the code and test results produced so far for correctness and quality. Do "
        "not rewrite the implementation yourself. Give a short go/no-go verdict and why."
    ),
}


def build_prompt(
    role: RoleSpec,
    manifest: WorkflowManifest,
    blackboard: Blackboard,
    extra_context: str = "",
) -> str:
    decisions = blackboard.read_decisions()
    decisions_str = "\n".join(f"- [{d['role']}] {d['summary']}" for d in decisions) or "(none yet)"
    framing = ROLE_TASK_FRAMING.get(role.agent_profile, f"Perform your role as {role.name}.")

    parts = [
        f"You are '{role.name}' on an autonomous engineering team. Team goal: {manifest.project.goal}",
        "",
        "Decisions logged by the team so far:",
        decisions_str,
        "",
        f"Your task: {framing}",
    ]
    if extra_context:
        parts += ["", extra_context]
    parts += [
        "",
        "End your final message with a line starting with 'SUMMARY:' — one sentence for the team log.",
    ]
    return "\n".join(parts)


def extract_summary(final_text: str) -> str:
    for line in final_text.splitlines():
        if line.strip().upper().startswith("SUMMARY:"):
            return line.split(":", 1)[1].strip()
    for line in final_text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return "(no summary produced)"


def extract_qa_result(final_text: str) -> bool | None:
    for line in final_text.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("RESULT: PASS"):
            return True
        if stripped.startswith("RESULT: FAIL"):
            return False
    return None


def run_workflow(project_dir: Path, manifest: WorkflowManifest, blackboard: Blackboard) -> bool:
    """Sequential execution in dependency order. Returns True iff every role
    completed without error and QA (if present) reported PASS. Phase-1 cut:
    a QA failure stops the run rather than looping — loop_engine.py (Phase 3)
    replaces this with a bounded Backend<->QA retry.
    """
    for role in manifest.execution_order():
        blackboard.update_status(role.name, "working", current_task=role.agent_profile)
        prompt = build_prompt(role, manifest, blackboard)
        result = run_vibe_role(prompt=prompt, agent=role.agent_profile, workdir=project_dir)
        _write_role_log(project_dir, role.name, result)

        if not result.success:
            blackboard.update_status(role.name, "error", current_task=result.error)
            blackboard.publish_decision(role.name, f"ERROR: {result.error}")
            return False

        summary = extract_summary(result.final_text)

        if role.agent_profile == "qa" and extract_qa_result(result.final_text) is False:
            blackboard.publish_decision(role.name, f"FAIL: {summary}")
            blackboard.update_status(role.name, "blocked", current_task="tests failing")
            return False

        blackboard.publish_decision(role.name, summary)
        blackboard.update_status(role.name, "done", current_task=summary)

    return True
