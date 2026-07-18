import { useEffect, useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";
import type { LogEntry, Manifest } from "../types";

const POLL_MS = 2000;

export function LogsView({ manifest }: { manifest: Manifest }) {
  const roles = manifest.roles.map((r) => r.name);
  const [selected, setSelected] = useState<string>(roles[0] ?? "Planner");
  const [entries, setEntries] = useState<LogEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetch(`${API_BASE}/logs?role=${encodeURIComponent(selected)}`)
        .then((r) => r.json())
        .then((payload) => {
          if (!cancelled) setEntries(payload.entries ?? []);
        })
        .catch(() => {});
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [selected]);

  return (
    <div className="tab-view">
      <div className="role-chips">
        {(roles.length > 0 ? roles : [selected]).map((role) => (
          <button
            key={role}
            className={`role-chip${role === selected ? " role-chip--active" : ""}`}
            onClick={() => setSelected(role)}
          >
            {role}
          </button>
        ))}
      </div>
      {entries.length === 0 ? (
        <p className="panel-section__empty">No log entries for {selected} yet.</p>
      ) : (
        <ul className="log-list">
          {entries.map((entry, i) => (
            <li key={i} className={`log-entry log-entry--${entry.role}`}>
              <span className="log-entry__badge">
                {entry.role === "tool" ? (entry.tool_name ?? "tool") : entry.role}
              </span>
              {entry.tools.length > 0 && (
                <span className="log-entry__tools">calls: {entry.tools.join(", ")}</span>
              )}
              {entry.content && <pre className="log-entry__content">{entry.content}</pre>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
