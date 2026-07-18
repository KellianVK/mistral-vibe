import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentNodeData } from "../lib/buildGraph";

const STATUS_COLOR: Record<AgentNodeData["status"], string> = {
  idle: "var(--text-faint)",
  working: "var(--accent)",
  done: "var(--success)",
  blocked: "var(--warning)",
  error: "var(--error)",
};

const STATUS_LABEL: Record<AgentNodeData["status"], string> = {
  idle: "idle",
  working: "working",
  done: "done",
  blocked: "blocked",
  error: "error",
};

function TimingChips({ data }: { data: AgentNodeData }) {
  const timing = data.timing;
  if (!timing) return null;
  const chips: string[] = [];
  if (timing.first_action_s != null) chips.push(`first act ${timing.first_action_s.toFixed(1)}s`);
  if (timing.turns > 0) chips.push(`${timing.turns} turns`);
  if (timing.total_s != null) chips.push(`total ${Math.round(timing.total_s)}s`);
  if (chips.length === 0) return null;
  return (
    <div className="agent-node__timing" title="first action latency · turns · wall-clock">
      {chips.map((chip) => (
        <span key={chip} className="agent-node__timing-chip">
          {chip}
        </span>
      ))}
    </div>
  );
}

export function AgentNode({ data }: NodeProps & { data: AgentNodeData }) {
  const color = STATUS_COLOR[data.status];

  return (
    <div className={`agent-node agent-node--${data.status}`} style={{ borderColor: color }}>
      <Handle type="target" position={Position.Top} className="agent-node__handle" />

      <div className="agent-node__head">
        <span className="agent-node__dot" style={{ background: color }} aria-hidden="true" />
        <span className="agent-node__role">{data.role}</span>
        {data.hasRecentDecision && <span className="agent-node__decision-marker" title="Recent decision" />}
      </div>

      <div className="agent-node__status" style={{ color }}>
        {STATUS_LABEL[data.status]}
      </div>

      {data.status === "blocked" && data.blockedOn ? (
        <div className="agent-node__blocked">
          <span className="agent-node__blocked-label">waiting on {data.blockedOn.target}</span>
          <span className="agent-node__task">{data.blockedOn.question}</span>
        </div>
      ) : (
        data.task && <div className="agent-node__task">{data.task}</div>
      )}

      <TimingChips data={data} />

      <Handle type="source" position={Position.Bottom} className="agent-node__handle" />
    </div>
  );
}
