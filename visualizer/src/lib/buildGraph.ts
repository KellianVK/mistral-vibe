import type { Edge, Node } from "@xyflow/react";
import type { AgentStatus, BlackboardState, Manifest, RoleTiming } from "../types";

export interface AgentNodeData extends Record<string, unknown> {
  role: string;
  status: AgentStatus;
  task: string | null;
  hasRecentDecision: boolean;
  blockedOn: { target: string; question: string } | null;
  timing: RoleTiming | null;
}

export type AgentFlowNode = Node<AgentNodeData, "agent">;

const WAVE_GAP_Y = 230;
const NODE_GAP_X = 300;

/**
 * Group roles into dependency levels (Kahn's algorithm): every role in a
 * wave has all of its depends_on satisfied by earlier waves. Mirrors the
 * orchestrator's execution order so the graph reads top-to-bottom in time.
 */
function dependencyWaves(manifest: Manifest): string[][] {
  const remaining = new Map(manifest.roles.map((r) => [r.name, r.depends_on]));
  const placed = new Set<string>();
  const waves: string[][] = [];

  while (remaining.size > 0) {
    const ready = [...remaining.entries()]
      .filter(([, deps]) => deps.every((d) => placed.has(d) || !remaining.has(d)))
      .map(([name]) => name);
    if (ready.length === 0) {
      // Circular manifest — render the leftovers as one final wave rather
      // than looping forever; the server validates cycles for real.
      waves.push([...remaining.keys()]);
      break;
    }
    waves.push(ready);
    for (const name of ready) {
      placed.add(name);
      remaining.delete(name);
    }
  }
  return waves;
}

/**
 * Pure (manifest, state) -> React Flow nodes/edges. Same inputs always give
 * the same graph; all liveness comes from the state snapshot, never a clock.
 */
export function buildGraph(
  manifest: Manifest,
  state: BlackboardState,
): { nodes: AgentFlowNode[]; edges: Edge[] } {
  const waves = dependencyWaves(manifest);
  const latestDecision = state.decisions.length > 0 ? state.decisions[state.decisions.length - 1] : null;

  const nodes: AgentFlowNode[] = [];
  waves.forEach((wave, waveIndex) => {
    const rowWidth = (wave.length - 1) * NODE_GAP_X;
    wave.forEach((roleName, i) => {
      const agent = state.agents[roleName];
      const status: AgentStatus = agent?.status ?? "idle";
      const openQuestion = state.questions.find((q) => q.from === roleName && !q.resolved);
      nodes.push({
        id: roleName,
        type: "agent",
        position: { x: i * NODE_GAP_X - rowWidth / 2, y: waveIndex * WAVE_GAP_Y },
        data: {
          role: roleName,
          status,
          task: agent?.current_task ?? null,
          hasRecentDecision: latestDecision?.role === roleName,
          blockedOn:
            status === "blocked" && openQuestion
              ? { target: openQuestion.to, question: openQuestion.question }
              : null,
          timing: state.timings?.[roleName] ?? null,
        },
      });
    });
  });

  const roleNames = new Set(manifest.roles.map((r) => r.name));
  const edges: Edge[] = [];
  for (const role of manifest.roles) {
    for (const dep of role.depends_on) {
      if (!roleNames.has(dep)) continue;
      const sourceStatus = state.agents[dep]?.status ?? "idle";
      const targetStatus = state.agents[role.name]?.status ?? "idle";
      const active = sourceStatus === "working" || targetStatus === "working";
      const waiting =
        targetStatus === "blocked" &&
        state.questions.some((q) => q.from === role.name && q.to === dep && !q.resolved);
      edges.push({
        id: `${dep}->${role.name}`,
        source: dep,
        target: role.name,
        animated: active || waiting,
        className: waiting ? "team-edge team-edge--waiting" : "team-edge",
        label: waiting ? "waiting" : undefined,
      });
    }
  }

  return { nodes, edges };
}
