import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./styles.css";

const roles = ["Planner", "Backend", "QA"];
const positions = {
  Planner: { x: 0, y: 0 },
  Backend: { x: 360, y: 0 },
  QA: { x: 720, y: 0 },
};

function AgentNode({ data }) {
  const status = data.status;
  return (
    <article className={`agent-card agent-card--${status.state}`}>
      {status.role !== "Planner" && (
        <Handle type="target" position={Position.Left} />
      )}
      <div className="agent-card__eyebrow">
        <span className="state-dot" />
        {status.state}
      </div>
      <h2>{status.role}</h2>
      <p>{status.current_task}</p>
      <footer>
        <span>{status.decision_count} decisions</span>
        <span>{data.model}</span>
      </footer>
      {status.role !== "QA" && (
        <Handle type="source" position={Position.Right} />
      )}
    </article>
  );
}

const nodeTypes = { agent: AgentNode };

function emptyStatus(role) {
  return {
    role,
    state: "idle",
    current_task: "Waiting to start",
    updated_at: "",
    decision_count: 0,
  };
}

function App() {
  const [snapshot, setSnapshot] = useState({
    state: "idle",
    goal: null,
    error: null,
    agents: [],
    model: "mistral-medium-3.5",
    generated_at: null,
  });
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    let active = true;

    async function refresh() {
      try {
        const response = await fetch(
          `/api/status?token=${encodeURIComponent(token ?? "")}`,
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = await response.json();
        if (active) {
          setSnapshot(next);
          setConnected(true);
        }
      } catch {
        if (active) setConnected(false);
      }
    }

    refresh();
    const interval = window.setInterval(refresh, 1000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const statuses = useMemo(() => {
    const byRole = new Map(snapshot.agents.map((agent) => [agent.role, agent]));
    return roles.map((role) => byRole.get(role) ?? emptyStatus(role));
  }, [snapshot.agents]);

  const nodes = statuses.map((status) => ({
    id: status.role,
    type: "agent",
    position: positions[status.role],
    data: { status, model: snapshot.model },
    draggable: true,
  }));
  const edges = [
    { id: "planner-backend", source: "Planner", target: "Backend" },
    { id: "backend-qa", source: "Backend", target: "QA" },
  ].map((edge) => ({
    ...edge,
    animated:
      statuses.find((status) => status.role === edge.target)?.state === "working",
    className: "workflow-edge",
  }));

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <div className="brand">VIBE WORKFLOW</div>
          <h1>{snapshot.goal ?? "No workflow started"}</h1>
        </div>
        <div className="workflow-state">
          <span className={`state-dot state-dot--${snapshot.state}`} />
          {snapshot.state}
        </div>
      </header>

      {snapshot.error && <aside className="error-banner">{snapshot.error}</aside>}

      <section className="flow-panel">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          nodesConnectable={false}
          elementsSelectable
          fitView
          fitViewOptions={{ padding: 0.22 }}
          minZoom={0.55}
          maxZoom={1.5}
          proOptions={{ hideAttribution: false }}
        >
          <Background color="#334155" gap={26} size={1.4} />
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) => {
              const state = node.data?.status?.state;
              return {
                done: "#34d399",
                working: "#a78bfa",
                blocked: "#fb7185",
                idle: "#64748b",
              }[state] ?? "#64748b";
            }}
          />
          <Controls showInteractive={false} />
        </ReactFlow>
      </section>

      <footer className="dashboard-footer">
        <span>{connected ? "Live · refreshes every second" : "Reconnecting…"}</span>
        <span>Read-only localhost dashboard</span>
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
