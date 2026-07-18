from pathlib import Path

from mistral_workflow.blackboard.store import Blackboard
from mistral_workflow.planner import run_single_role
from mistral_workflow.roles import RoleSpec, WorkflowManifest


def run_workflow(project_dir: Path, manifest: WorkflowManifest, blackboard: Blackboard) -> bool:
    """Like planner.run_workflow, but a QA role that doesn't pass triggers a
    bounded retry of the roles it depends on (Backend) before giving up,
    republishing the failure as Blackboard context each time so the retried
    role can see exactly what broke. Never exceeds
    manifest.project.max_loop_iterations retries for a given QA gate.
    """
    for role in manifest.execution_order():
        outcome = run_single_role(project_dir, manifest, blackboard, role)

        if not outcome.success:
            return False

        if role.agent_profile == "qa" and outcome.qa_passed is not True:
            if not retry_qa_gate(project_dir, manifest, blackboard, role):
                return False

    return True


def retry_qa_gate(
    project_dir: Path,
    manifest: WorkflowManifest,
    blackboard: Blackboard,
    qa_role: RoleSpec,
) -> bool:
    """Re-run qa_role's upstream roles (typically just Backend) then qa_role
    itself, up to max_loop_iterations times. Returns True once QA passes;
    marks the gate 'blocked' and returns False if the ceiling is hit first.
    """
    max_iterations = manifest.project.max_loop_iterations
    upstream = [manifest.role(name) for name in qa_role.depends_on]
    if not upstream:
        blackboard.update_status(qa_role.name, "blocked", current_task="no upstream role to retry")
        return False

    upstream_names = ", ".join(r.name for r in upstream)

    for iteration in range(1, max_iterations + 1):
        blackboard.publish_decision(
            "loop_engine",
            f"Iteration {iteration}/{max_iterations}: re-running {upstream_names} to address the {qa_role.name} failure above.",
        )

        for role in upstream:
            outcome = run_single_role(project_dir, manifest, blackboard, role)
            if not outcome.success:
                return False

        outcome = run_single_role(project_dir, manifest, blackboard, qa_role)
        if not outcome.success:
            return False
        if outcome.qa_passed is True:
            blackboard.publish_decision(
                "loop_engine", f"{qa_role.name} passed after {iteration} loop iteration(s)."
            )
            return True

    blackboard.update_status(
        qa_role.name, "blocked", current_task=f"still failing after {max_iterations} loop iteration(s)"
    )
    blackboard.publish_decision(
        "loop_engine",
        f"BLOCKED: {qa_role.name} did not pass within max_loop_iterations={max_iterations}. Stopping.",
    )
    return False
