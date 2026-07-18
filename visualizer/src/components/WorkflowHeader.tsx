import miaouLogo from "../assets/miaou.svg";
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

function teamWallClock(state: BlackboardState): number | null {
  const totals = Object.values(state.timings ?? {})
    .map((t) => t.total_s)
    .filter((t): t is number => t != null);
  return totals.length > 0 ? Math.max(...totals) : null;
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
  const wallClock = teamWallClock(state);

  return (
    <header className="workflow-header">
      <div className="workflow-header__identity">
        <span className="workflow-header__brand">
          <img className="workflow-header__logo" src={miaouLogo} alt="" aria-hidden="true" />
          <span className="workflow-header__name">MiaouFlow</span>
          <span className="workflow-header__by">vibe workflow</span>
        </span>
        <h1 className="workflow-header__goal">{manifest.project?.goal ?? "No workflow running"}</h1>
      </div>

      <div className="workflow-header__signals">
        <span className={`pill pill--run-${runStatus}`}>{RUN_STATUS_LABEL[runStatus]}</span>
        <span className="workflow-header__count">
          {doneCount}/{roles.length || 0} done
        </span>
        {wallClock != null && (
          <span className="pill pill--timing" title="Longest agent wall-clock this run">
            {Math.round(wallClock)}s
          </span>
        )}
        <span className={`pill pill--conn-${connection}`}>
          <span className="pill__dot" aria-hidden="true" />
          {CONNECTION_LABEL[connection]}
        </span>
      </div>
    </header>
  );
}
