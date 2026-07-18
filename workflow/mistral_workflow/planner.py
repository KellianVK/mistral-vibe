import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mistral_workflow.blackboard.store import Blackboard
from mistral_workflow.roles import RoleSpec, WorkflowManifest
from mistral_workflow.vibe_runner import VibeResult, run_vibe_role

MAX_FAIL_DETAIL_CHARS = 2000


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
        "working code and create whatever files are needed. Do NOT write or run tests "
        "yourself — that is QA's job, done independently; a bug and its own 'proof' coming "
        "from the same author defeats the point of having separate roles. If the log below "
        "includes a QA failure, fix that specific failure — read the failing test output "
        "carefully and address the root cause rather than starting over."
    ),
    "qa": (
        "Write your OWN tests for the code Backend just produced, independent of anything "
        "Backend may have already written — do not just trust or re-run existing tests. "
        "Base each test on what the function's name and the team's goal say it should do "
        "(e.g. a function called add(a, b) must satisfy add(2, 3) == 5), not on whatever the "
        "current implementation happens to return. Actually execute the test suite with a "
        "shell command rather than just reading the code. Your final message MUST start with "
        "a line that is exactly 'RESULT: PASS' or 'RESULT: FAIL', followed by the actual test "
        "output (assertion errors, expected vs actual) so a teammate could diagnose a failure "
        "without re-running anything."
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


@dataclass
class RoleOutcome:
    success: bool  # the vibe subprocess itself ran without crashing/timing out
    qa_passed: bool | None  # only set when role.agent_profile == "qa"
    summary: str


def run_single_role(
    project_dir: Path, manifest: WorkflowManifest, blackboard: Blackboard, role: RoleSpec
) -> RoleOutcome:
    """Run one role's headless Vibe turn and record its outcome on the Blackboard."""
    blackboard.update_status(role.name, "working", current_task=role.agent_profile)
    prompt = build_prompt(role, manifest, blackboard)
    result = run_vibe_role(prompt=prompt, agent=role.agent_profile, workdir=project_dir)
    _write_role_log(project_dir, role.name, result)

    if not result.success:
        blackboard.update_status(role.name, "error", current_task=result.error)
        blackboard.publish_decision(role.name, f"ERROR: {result.error}")
        return RoleOutcome(success=False, qa_passed=None, summary=result.error or "unknown error")

    summary = extract_summary(result.final_text)
    qa_passed = extract_qa_result(result.final_text) if role.agent_profile == "qa" else None

    if role.agent_profile == "qa" and qa_passed is not True:
        # Publish the full test output (not just the one-line summary) so a
        # retried Backend has the actual failure to diagnose, not a vague
        # "tests failed". Also covers a garbled/incomplete QA turn (seen in
        # practice: the model emitting a malformed tool call as plain text
        # and stopping) — "no clear RESULT: PASS" is never read as a pass.
        detail = result.final_text.strip()[:MAX_FAIL_DETAIL_CHARS] or summary
        blackboard.publish_decision(role.name, f"FAIL: {detail}")
        blackboard.update_status(role.name, "blocked", current_task="tests failing or inconclusive")
        return RoleOutcome(success=True, qa_passed=False, summary=summary)

    blackboard.publish_decision(role.name, summary)
    blackboard.update_status(role.name, "done", current_task=summary)
    return RoleOutcome(success=True, qa_passed=qa_passed, summary=summary)


def run_workflow(project_dir: Path, manifest: WorkflowManifest, blackboard: Blackboard) -> bool:
    """Sequential execution in dependency order, no retries. Returns True iff
    every role completed without error and QA (if present) reported PASS.
    Kept as a simple building block; `mistral workflow run` uses
    loop_engine.run_workflow, which adds the bounded Backend<->QA retry.
    """
    for role in manifest.execution_order():
        outcome = run_single_role(project_dir, manifest, blackboard, role)
        if not outcome.success:
            return False
        if role.agent_profile == "qa" and outcome.qa_passed is not True:
            return False
    return True
