# Miaou Flow (`vibe workflow`) — Team Build Brief

**Paris Mistral Vibe Hackathon — Saturday, July 18, 2026 — submission 6:00 PM**

---

## 0. One-liner

> **`vibe workflow`: the orchestration layer Mistral Vibe is missing.** One command spawns a named team of Vibe agents (Planner, Backend, Frontend, QA) that coordinate through a shared Blackboard — a structured memory where they publish decisions, ask each other questions, and unblock each other — all visible live on a web board.

**The differentiator is NOT "multi-agent"** (Claude Code and Codex already do parallel agents). The differentiator is the **Blackboard**: agents that *negotiate interfaces in real time instead of colliding at merge time* — and a live board where the jury watches it happen.

---

## 1. Scope — what's in, what's out

### MVP — must work live by 5:00 PM
| # | Component | Definition of done |
|---|-----------|-------------------|
| 1 | **Blackboard daemon + MCP bridge** | 4 tools callable from a real Vibe session; state survives across calls |
| 2 | **Orchestrator CLI** | `vibe-squad init` and `vibe-squad run --goal "..."` spawn 3 headless Vibe agents in git worktrees |
| 3 | **Live web board** | Browser page auto-updates: agent cards (status/task), decision feed, blocked-on links |
| 4 | **Demo repo + golden path** | The Todo-API scenario runs end-to-end at least twice in a row |

### Stretch — only if MVP is green by ~3:30 PM (in priority order)
1. `vibe-squad status` — terminal print of the blackboard state (cheap: same data as the board)
2. QA as a 4th role + one quality gate (`after_tool` hook runs pytest; `before_tool` blocks `git push` until QA validates in the Blackboard)
3. Reviewer-led merge of the worktrees (otherwise the orchestrator merges mechanically)

### Cut — pitch as roadmap, do not build
Loop Engine / iteration caps · `memory --replay` · cloud/teleport agents · Connectors (GitHub/Jira/Slack) · Security/DevOps/Docs roles · TUI graph view. These live in `workflow.toml` as commented-out roles and in one deck slide titled "What this becomes."

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  vibe-squad CLI (Python)                                     │
│  init · run --goal · status                                  │
└──────────────┬───────────────────────────────────────────────┘
               │ 1. Planner call (Mistral API, Medium 3.5)
               │    goal → role task assignments
               ▼
   spawns N headless sessions:  vibe -p "<role prompt + task>"
   each in its own git worktree, each with role agent profile
               │
   ┌───────────┼───────────────┐
   ▼           ▼               ▼
 Backend    Frontend         (QA)          ← Vibe agents (Devstral 2)
   │           │               │
   │  MCP stdio bridge (thin client, spawned per call by Vibe)
   ▼           ▼               ▼
┌──────────────────────────────────────────────────────────────┐
│  Blackboard daemon — FastAPI on localhost:8765               │
│  state: agents · decisions · questions · file claims         │
│  REST for the MCP bridge · WebSocket for the board           │
└──────────────┬───────────────────────────────────────────────┘
               ▼
        Live web board (single index.html + WS)
```

### Key design decision — read this before coding
**Vibe spawns stdio MCP servers per tool call, so the MCP process CANNOT hold state.** Therefore the Blackboard is split in two:
- a **persistent FastAPI daemon** (owns all state, in-memory dict + JSON dump to disk for crash recovery),
- a **stateless MCP stdio bridge** (`blackboard_mcp.py`) that Vibe spawns; it just forwards each tool call to the daemon over HTTP and returns the response.

This also gives the web board its data source for free (same daemon, WebSocket endpoint). Do not try to make the MCP server itself stateful — it will silently lose everything.

### Model routing
- **Planner**: Mistral Medium 3.5 (one-shot decomposition, needs reasoning)
- **All coding roles**: Devstral 2 (it's the agentic coding model; also scores points as "best use of Mistral Vibe")

---

## 3. Blackboard API (contract — freeze this at kickoff)

### MCP tools exposed to agents
```
publish_decision(role, topic, summary, artifact?)   # "auth-contract": "POST /auth/login returns {token, expires_in}"
read_decisions(topic?)                              # returns all decisions, optionally filtered by topic
request_review(role, target_role, question)         # direct question to another role; also marks asker "blocked"
answer_question(role, question_id, answer)          # unblocks the asker
update_status(role, state, current_task)            # state ∈ {idle, working, blocked, done}
claim_file(role, path) / release_file(role, path)   # soft lock; returns conflict if already claimed
```

### Daemon REST endpoints (used by MCP bridge and orchestrator)
```
POST /decisions        GET /decisions?topic=
POST /questions        POST /questions/{id}/answer     GET /questions?open=true
POST /status           GET /agents
POST /claims           DELETE /claims
GET  /state            # full snapshot (board initial load)
WS   /ws               # pushes every state change (board live updates)
```

### State model (in-memory dict, JSON-dumped after each write)
```json
{
  "agents":    {"backend": {"state": "working", "task": "...", "blocked_on": null}},
  "decisions": [{"id": 1, "role": "backend", "topic": "auth-contract", "summary": "...", "ts": "..."}],
  "questions": [{"id": 1, "from": "frontend", "to": "backend", "question": "...", "answer": null}],
  "claims":    {"src/api/auth.py": "backend"}
}
```

---

## 4. Config formats

### `.vibe/workflow.toml` (generated by `vibe-squad init`)
```toml
[workflow]
goal_default = ""
max_agents = 4

[[roles]]
name = "backend"
model = "devstral-2"
objective = "Implement server-side features. Publish every API contract to the Blackboard BEFORE implementing it."
worktree = "backend"

[[roles]]
name = "frontend"
model = "devstral-2"
objective = "Implement the UI. Before consuming any API, read_decisions(topic='auth-contract') or request_review(backend, ...)."
worktree = "frontend"

# [[roles]]  name = "qa"        ← stretch
# [[roles]]  name = "security"  ← roadmap (shown in pitch)
# [[roles]]  name = "devops"    ← roadmap
```

### Per-role Vibe agent profile (`.vibe/agents/<role>.toml`)
Contains: the Blackboard MCP bridge under `[[mcp_servers]]`, the role's system-prompt addendum, and tool permissions. **The system-prompt addendum is what makes agents actually use the Blackboard** — treat it as code, iterate on it:

> You are the {role} agent in a team. RULES: (1) On start and before any shared-interface work, call `read_decisions`. (2) Publish every architectural/API decision with `publish_decision` before implementing it. (3) If you need something from another role, `request_review` and set yourself blocked — do not invent the answer. (4) `claim_file` before editing, `release_file` after. (5) `update_status` at every transition.

---

## 5. Ownership & workstreams

Assumes 4 people; with 3, person D's tasks fold into A (demo repo) and C (deck).

| Person | Owns | Deliverable | Key interface |
|--------|------|-------------|---------------|
| **A — Orchestrator** | `vibe-squad` CLI: Planner call, worktree creation, spawning/monitoring `vibe -p` processes, final merge | `init`, `run --goal` working | Reads daemon `GET /state` to know when agents are done |
| **B — Blackboard** | FastAPI daemon + MCP stdio bridge + state model | The 6 MCP tools callable from a real Vibe session | The API contract in §3 (frozen at kickoff) |
| **C — Board** | `index.html` + WebSocket client: agent cards, decision feed, blocked-on arrows, question popups | Board renders live during a real run | Daemon `GET /state` + `WS /ws` |
| **D — Demo & pitch** | Demo repo (Todo API skeleton), role prompt tuning, golden-path rehearsal, backup screen recording, deck | Scenario runs twice in a row + 2-min pitch ready | Everyone |

**B finishes first by design** (the daemon is the simplest component) → B then pairs with A on the riskiest part: making spawned Vibe agents reliably call the MCP tools (prompt tuning with D).

**Integration rule:** the §3 API contract is frozen after hour 1. Any change requires all four to agree — this is a hackathon, not a refactoring exercise.

---

## 6. Timeline (adjust clock to actual start; deadline 6:00 PM)

| Time | Milestone | Gate |
|------|-----------|------|
| **H0–H1** | **Validation spikes, all hands:** (a) `vibe -p` headless with project-local config + `--agent` profile; (b) a hello-world MCP stdio server gets called by Vibe; (c) two Vibe instances in parallel worktrees. Freeze §3 contract. | **If (b) fails → PIVOT** (see §7) |
| H1–H3 | A: spawn pipeline · B: daemon + bridge complete · C: board skeleton with fake data · D: demo repo + role prompts | B's tools callable from Vibe by H3 |
| H3–H4:30 | First integrated run: 2 agents, real goal, board live. Expect chaos; iterate on role prompts (D+B). | One full run completes |
| H4:30–5 | Golden path hardened (run it 3×). Decide stretch: QA role + gate, or stop. | Scenario is repeatable |
| H5–H5:30 | **Record the backup video** of a clean run. Polish board visuals. | Backup exists |
| H5:30–6 | README, deck (5 slides max), submit. | Submitted before 6:00 PM |
| Post-submit | Rehearse the live demo 2× with the recording as fallback. | |

---

## 7. Risks & pivots

| Risk | Mitigation |
|------|-----------|
| Vibe won't call our MCP tools (spike b fails) | **Pivot at H1, don't fight it:** keep daemon + board, drop MCP; agents read/write the blackboard via a `bash` tool call to a tiny CLI (`bb publish ...`). Uglier, same demo. |
| Agents ignore the Blackboard (prompt problem) | D iterates on role prompts from H3; worst case, the Planner's task descriptions explicitly say "your first action is read_decisions". |
| Headless runs are slow/flaky live | Golden-path scenario is small (Todo API, ~3 files/role); backup recording is mandatory, recorded at H5, not at 5:55. |
| Merge conflicts between worktrees | Roles own disjoint directories (`server/`, `web/`); `claim_file` covers the rest; orchestrator merges mechanically. |
| Two people blocked on the same bug | Ownership table above; B floats after H3. |
| Vibe version surprises (it ships weekly) | Pin the installed version at H0 (`uv tool install mistral-vibe==<today's>`); everyone uses the same. |

---

## 8. Demo script (3–4 min, two windows side by side)

1. **Terminal:** `vibe-squad run --goal "Todo API with JWT auth"` — Planner output shows the decomposition. (30 s)
2. **Board:** three cards go *working*. Narrate nothing — let it live. (30 s)
3. **The money shot:** Frontend card flips to **blocked — waiting on: backend/auth-contract**. Seconds later, Backend's `publish_decision("auth-contract", ...)` appears in the feed → Frontend unblocks and codes against the real contract. Say one sentence: *"No merge conflict, no hallucinated API — they negotiated."* (60 s)
4. Run completes → merged repo, tests green in the terminal. (30 s)
5. Close on the decision feed: *"and the team left minutes — this feed is the project's design log."* (20 s)
6. If anything hangs live → switch to the backup recording without apologizing.

---

## 9. Pitch skeleton (2 min, maps to judging criteria)

1. **Gap (creativity/uniqueness):** "Multi-agent is solved — Claude Code and Codex run parallel agents. What nobody ships is agents that *coordinate*: today's subagents can't talk to each other, so parallel work means merge-time collisions. Vibe has every primitive — subagents, worktrees, MCP, profiles — but no coordination layer."
2. **Demo (technical implementation):** the §8 script. Emphasize: built *on top of* Vibe's stable primitives, zero forks, the Blackboard is a standard MCP server any Vibe user can adopt today.
3. **Roadmap (future potential):** one slide — quality gates via hooks, Loop Engine, Reviewer merges, replay-as-documentation, cloud agents for heavy roles. "We're opening an issue/PR on `mistralai/mistral-vibe` today — this belongs upstream." *(Actually open the issue before the pitch — it's 10 minutes and it's proof.)*
4. **Close:** "Vibe today is an agent. `vibe workflow` makes it a team."

---

## 10. Deliverables checklist

- [ ] GitHub repo: orchestrator + daemon + bridge + board + demo project + `workflow.toml`
- [ ] README: install (`pipx install`-able or `uv run`), quickstart, architecture diagram (§2), roadmap
- [ ] Backup screen recording of a clean run
- [ ] Deck: 5 slides (gap → what we built → architecture → roadmap → close)
- [ ] Issue opened on `mistralai/mistral-vibe` proposing the Blackboard/workflow layer
- [ ] Submitted on the hackathon platform **before 6:00 PM**
