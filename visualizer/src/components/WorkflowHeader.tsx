import type { BlackboardState, ConnectionState, Manifest } from "../types";

type RunStatus = "idle" | "running" | "blocked" | "done";

function computeRunStatus(manifest: Manifest, state: BlackboardState): RunStatus {
  const roles = manifest.roles.map((r) => r.name);
  if (roles.length === 0) return "idle";
  const statuses = roles.map((r) => state.agents[r]?.status ?? "idle");
  if (statuses.some((s) => s === "blocked" || s === "error")) return "blocked";
  if (statuses.every((s) => s === "done")) return "done";
  if (statuses.some((s) => s === "working" || s === "done")) return "running";
  return "idle";
}

const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  idle: "Idle",
  running: "Running",
  blocked: "Blocked",
  done: "Complete",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  live: "Live",
  reconnecting: "Reconnecting",
  offline: "Offline",
};

export function WorkflowHeader({
  manifest,
  state,
  connection,
}: {
  manifest: Manifest;
  state: BlackboardState;
  connection: ConnectionState;
}) {
  const roles = manifest.roles.map((r) => r.name);
  const doneCount = roles.filter((r) => state.agents[r]?.status === "done").length;
  const runStatus = computeRunStatus(manifest, state);

  return (
    <header className="workflow-header">
      <div className="workflow-header__identity">
        <span className="workflow-header__brand">mistral workflow</span>
        <h1 className="workflow-header__goal">{manifest.project?.goal ?? "No project initialized"}</h1>
      </div>

      <div className="workflow-header__signals">
        <span className={`pill pill--run-${runStatus}`}>{RUN_STATUS_LABEL[runStatus]}</span>
        <span className="workflow-header__count">
          {doneCount}/{roles.length || 0} done
        </span>
        <span className={`pill pill--conn-${connection}`}>
          <span className="pill__dot" aria-hidden="true" />
          {CONNECTION_LABEL[connection]}
        </span>
      </div>
    </header>
  );
}
