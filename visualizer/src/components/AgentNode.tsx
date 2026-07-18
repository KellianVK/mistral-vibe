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

      <Handle type="source" position={Position.Bottom} className="agent-node__handle" />
    </div>
  );
}
