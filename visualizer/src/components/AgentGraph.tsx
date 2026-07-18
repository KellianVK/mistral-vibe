import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AgentState, AgentStatus, Manifest } from "../hooks/useWorkflowState";

const STATUS_COLOR: Record<AgentStatus, string> = {
  idle: "#9ca3af",
  working: "#3b82f6",
  done: "#22c55e",
  blocked: "#f59e0b",
  error: "#ef4444",
};

/** Longest-path level from any root (a role with no depends_on). Roles at
 * the same level are laid out side by side. Cycles fall back to level 0
 * for the offending role rather than recursing forever. */
function computeLevels(roles: Manifest["roles"]): Map<string, number> {
  const byName = new Map(roles.map((r) => [r.name, r]));
  const levels = new Map<string, number>();

  function levelOf(name: string, path: Set<string>): number {
    const cached = levels.get(name);
    if (cached != null) return cached;
    if (path.has(name)) return 0;
    const role = byName.get(name);
    if (!role || role.depends_on.length === 0) {
      levels.set(name, 0);
      return 0;
    }
    const nextPath = new Set(path).add(name);
    const level = 1 + Math.max(...role.depends_on.map((d) => levelOf(d, nextPath)));
    levels.set(name, level);
    return level;
  }

  for (const r of roles) levelOf(r.name, new Set());
  return levels;
}

type RoleNodeData = { label: string; status: AgentStatus; task: string | null };

function RoleNode({ data }: NodeProps & { data: RoleNodeData }) {
  const color = STATUS_COLOR[data.status];
  return (
    <div
      className={data.status === "working" ? "role-node role-node--working" : "role-node"}
      style={{ borderColor: color }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div className="role-node__title">
        <span className="role-node__dot" style={{ background: color }} />
        <strong>{data.label}</strong>
      </div>
      <div className="role-node__status" style={{ color }}>
        {data.status}
      </div>
      {data.task && <div className="role-node__task">{data.task}</div>}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { role: RoleNode };

export function AgentGraph({
  manifest,
  agents,
}: {
  manifest: Manifest;
  agents: Record<string, AgentState>;
}) {
  const { nodes, edges } = useMemo(() => {
    const levels = computeLevels(manifest.roles);
    const countPerLevel = new Map<number, number>();

    const nodes: Node[] = manifest.roles.map((role) => {
      const level = levels.get(role.name) ?? 0;
      const indexInLevel = countPerLevel.get(level) ?? 0;
      countPerLevel.set(level, indexInLevel + 1);
      const agent = agents[role.name];
      const data: RoleNodeData = {
        label: role.name,
        status: agent?.status ?? "idle",
        task: agent?.current_task ?? null,
      };
      return {
        id: role.name,
        type: "role",
        position: { x: indexInLevel * 260, y: level * 150 },
        data,
      };
    });

    const edges: Edge[] = manifest.roles.flatMap((role) =>
      role.depends_on.map((dep) => {
        const depDone = agents[dep]?.status === "done";
        return {
          id: `${dep}->${role.name}`,
          source: dep,
          target: role.name,
          animated: agents[dep]?.status === "working",
          label: depDone ? undefined : `waiting: ${dep}`,
          style: { stroke: depDone ? "#22c55e" : "#4b5563" },
          labelStyle: { fill: "#9ca3af", fontSize: 10 },
          labelBgStyle: { fill: "#14161b" },
        };
      })
    );

    return { nodes, edges };
  }, [manifest, agents]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
    >
      <Background color="#2a2d34" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
