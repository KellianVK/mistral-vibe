# MiaouFlow — Assumed simplifications & next steps

An honest map of where v0.1 cuts corners, and what the next concrete steps
are. Everything here was a deliberate trade for a working, rehearsed demo on
hackathon day.

## Assumed simplifications

1. **Shared workdir + soft locks, not worktrees.** All agents edit one
   directory; `claim_file` is advisory (the tool warns on conflict but the
   filesystem does not enforce it). The original brief's per-role git
   worktrees + mechanical merge would give hard isolation at the cost of a
   live merge step.
2. **Wave parallelism, not free-running agents.** Roles whose dependencies
   are met run fully in parallel (Backend ∥ Frontend, then QA ∥ Security ∥
   Docs ∥ DevOps), but a wave must finish before the next starts. A slow
   agent holds the wave; there is no work-stealing or speculative start.
   Messaging *is* live within a wave (tools hit the shared SQLite board
   mid-turn) — the limitation is scheduling, not communication.
3. **Coordination is mostly prompt-discipline, with two hard gates.**
   RESOLVED in part (merge branch): per-role tool restrictions are now
   technical, not advisory — Planner/Reviewer cannot write files or run
   shell, Security cannot edit code (`disabled_tools` in the generated agent
   profiles); and `git push` is hard-gated by a native `pre_tool` hook
   (`vibe/workflow/hooks/guard_push.py`) that denies until the Reviewer
   publishes a `GO:` review-verdict. Still prompt-only: publishing contracts
   before implementing.
4. **Team composition is rules, not reasoning.** `init` composes the team
   from scan flags + fixed rules (always Planner/Reviewer, base 4, optional
   Frontend/DevOps). Letting the Planner itself propose the team from the
   goal is one API call away.
5. **Hot-join is next-run, not mid-run.** `POST /agents` registers a role
   that joins the *next* run. Injecting an agent into a live wave is
   possible (spawn a worker against the same DB mid-run) but unscheduled.
6. **Board transport is a 1 s diff-poll behind the WebSocket.** The server
   polls SQLite and pushes on change; a commit-hook (SQLite `update_hook`)
   would make pushes instant. The UI degrades to HTTP polling on WS failure.
7. **Timing metrics are orchestrator-stamped.** Turn boundaries come from
   the NDJSON stream at read time; provider-side token/latency numbers are
   not surfaced. There is no A/B toggle in the UI for warm-start vs cold
   (the `--no-warm-start` flag exists on the CLI).
8. **Change attribution is heuristic in parallel waves.** File changes are
   attributed via claims, falling back to the exiting worker's snapshot
   diff; two agents editing one unclaimed file in the same wave can
   misattribute a change.
9. **The QA fixture bug pattern.** Agent-written tests occasionally contain
   their own bugs (seen in rehearsal: an undeclared pytest fixture). The
   Reviewer role catches this class of problem — it reads results rather
   than trusting them — but only when included in the team.
10. **The quality loop retries blind on wall-clock.** RESOLVED for the core
   case (merge branch): a QA `FAIL:` verdict now re-runs the implementers
   with the failure in context, then re-runs QA, bounded by
   `--max-loop-iterations` (default 3), with each retry broadcast to the
   board. Remaining gap: the loop shares the run's single global deadline,
   so late failures may not leave time for a full retry.

## Next steps, prioritized

1. ~~Reviewer-gated `git push`~~ — DONE (merge branch): native `pre_tool`
   hook, provisioned automatically when the team includes a Reviewer.
2. **Planner-composed teams**: feed the goal + scan summary to the Planner
   model and let it emit the role list `init` provisions (hours).
3. ~~Retry loop~~ — DONE (merge branch): QA `FAIL:` re-runs implementers
   then QA, bounded by `max_loop_iterations`, logged via broadcast.
4. **Worktree isolation mode** for repos where soft locks are not enough:
   `--isolation worktree` per role + orchestrated merge at wave end.
5. **Mid-run agent injection** from the board, reusing the running
   orchestrator's deadline and DB.
6. **Instant board pushes** via SQLite `update_hook` → asyncio queue → WS.
7. **Cloud/teleport workers** for heavy roles; the orchestrator only needs
   a different spawn command per role.

## Merge notes (miaouflow-merged)

Ported from teammates' branches onto the `miaou-flow-v0.1` base:

- **Per-role tool restrictions** (pattern from `dev-kellian`'s
  `ENABLED_TOOLS_BY_ROLE`), expressed as `disabled_tools` in the generated
  Vibe agent profiles — enforcement by the harness, not the prompt.
- **Quality retry loop** (logic from `dev_evan_mistral`'s `loop_engine.py`),
  reimplemented against the native SQLite blackboard.
- **Reviewer push gate** (intent from `dev_evan_mistral`'s
  `hooks/guard_push.py`), reimplemented as a documented native Vibe
  `pre_tool` hook, fail-open except the explicit deny.
- **Dedicated prompts** for Security, DevOps, Docs, and Reviewer (previously
  the generic `custom.md` fallback), with the cost discipline from
  `dev-kellian`'s final `qa.md` (one final verdict per role, reuse existing
  tests, no redundant publications) applied to `qa.md` as well.
- **Deliberately not ported**: an in-session `/workflow` skill entry point
  (both teammate branches had one). The native `vibe workflow` subcommand is
  the demo path; a skill wrapper calling the same orchestrator is easy to
  add later but was cut for time. A global run-level price cap (per-worker
  caps exist) is also still open — worker costs are not yet aggregated.
