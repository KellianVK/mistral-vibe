import json
import sys
from pathlib import Path

# vibe's hook executor runs this as `pre_tool`/match="bash" — i.e. on EVERY
# bash call in the target project, not just `git push`. Any bug here that
# raises must fail OPEN (silent passthrough), or a broken guard would deny
# every single bash call in the demo. The one path that fails CLOSED is the
# specific rule this hook exists to enforce: an unapproved `git push`.


def main() -> None:
    try:
        invocation = json.load(sys.stdin)
        command = (invocation.get("tool_input") or {}).get("command", "")
        if "git push" not in command:
            return

        from mistral_workflow.blackboard.store import Blackboard, blackboard_path
        from mistral_workflow.planner import extract_reviewer_verdict

        project_dir = Path(invocation.get("cwd") or ".").resolve()
        decisions = Blackboard(blackboard_path(project_dir)).read_decisions("reviewer")
        approved = any(extract_reviewer_verdict(d["summary"]) is True for d in decisions)

        if not approved:
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": (
                            "Blocked by mistral-workflow: no GO decision from 'reviewer' on "
                            "the Blackboard yet. Run `mistral workflow status` to check."
                        ),
                    }
                )
            )
    except Exception:
        return


if __name__ == "__main__":
    main()
