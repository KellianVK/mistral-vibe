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
3. **Coordination is prompt-discipline, not protocol-enforcement.** Agents
   follow the blackboard protocol because their prompts say so and the tools
   make it cheap. Nothing *forces* Backend to publish a contract before
   coding. A `pre_tool` hook (Vibe supports them natively) could hard-gate
   `git push` on a Reviewer GO — wired in a teammate's branch, not ported.
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

## Next steps, prioritized

1. **Reviewer-gated `git push`** via a native `pre_tool` hook reading the
   `review-verdict` decision — closes the quality loop end-to-end (half a
   day; the hook payload plumbing already exists in vibe core).
2. **Planner-composed teams**: feed the goal + scan summary to the Planner
   model and let it emit the role list `init` provisions (hours).
3. **Retry loop**: on a QA `FAIL:` verdict, re-spawn the owning role with
   the failure in its warm-start brief, bounded by `max_loop_iterations`
   (the loop engine pattern exists in a teammate's branch).
4. **Worktree isolation mode** for repos where soft locks are not enough:
   `--isolation worktree` per role + orchestrated merge at wave end.
5. **Mid-run agent injection** from the board, reusing the running
   orchestrator's deadline and DB.
6. **Instant board pushes** via SQLite `update_hook` → asyncio queue → WS.
7. **Cloud/teleport workers** for heavy roles; the orchestrator only needs
   a different spawn command per role.
