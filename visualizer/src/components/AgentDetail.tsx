import { useEffect, useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";
import type { BlackboardState, LogEntry, Manifest } from "../types";

export function AgentDetail({
  role,
  manifest,
  state,
  onClose,
}: {
  role: string;
  manifest: Manifest;
  state: BlackboardState;
  onClose: () => void;
}) {
  const spec = manifest.roles.find((r) => r.name === role);
  const agent = state.agents[role];
  const timing = state.timings?.[role];
  const decisions = state.decisions.filter((d) => d.role === role);
  const messages = (state.messages ?? []).filter((m) => m.from === role || m.to === role);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetch(`${API_BASE}/logs?role=${encodeURIComponent(role)}`)
        .then((r) => r.json())
        .then((payload) => {
          if (!cancelled) setLogs((payload.entries ?? []).slice(-6));
        })
        .catch(() => {});
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [role]);

  return (
    <div className="agent-detail">
      <div className="agent-detail__head">
        <span className={`agent-detail__status agent-detail__status--${agent?.status ?? "idle"}`}>
          {agent?.status ?? "idle"}
        </span>
        <h2 className="agent-detail__title">{role}</h2>
        <button className="agent-detail__close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      {agent?.current_task && <p className="agent-detail__task">{agent.current_task}</p>}

      <dl className="agent-detail__facts">
        {spec?.model && (
          <>
            <dt>Model</dt>
            <dd>
              <code>{spec.model}</code>
            </dd>
          </>
        )}
        {spec && spec.depends_on.length > 0 && (
          <>
            <dt>Depends on</dt>
            <dd>{spec.depends_on.join(", ")}</dd>
          </>
        )}
        {timing?.first_action_s != null && (
          <>
            <dt>First action</dt>
            <dd>{timing.first_action_s.toFixed(1)}s</dd>
          </>
        )}
        {timing && timing.turns > 0 && (
          <>
            <dt>Turns</dt>
            <dd>{timing.turns}</dd>
          </>
        )}
        {timing?.total_s != null && (
          <>
            <dt>Wall-clock</dt>
            <dd>{Math.round(timing.total_s)}s</dd>
          </>
        )}
      </dl>

      {decisions.length > 0 && (
        <section className="agent-detail__section">
          <h3>Decisions ({decisions.length})</h3>
          <ul>
            {decisions.slice(-4).map((d, i) => (
              <li key={i}>
                {d.topic && <span className="feed__topic">{d.topic}</span>} {d.summary}
              </li>
            ))}
          </ul>
        </section>
      )}

      {messages.length > 0 && (
        <section className="agent-detail__section">
          <h3>Messages</h3>
          <ul>
            {messages.slice(-4).map((m) => (
              <li key={m.id}>
                <span className="agent-detail__msg-dir">{m.from === role ? `→ ${m.to}` : `← ${m.from}`}</span>{" "}
                {m.content}
              </li>
            ))}
          </ul>
        </section>
      )}

      {logs.length > 0 && (
        <section className="agent-detail__section">
          <h3>Recent activity</h3>
          <ul className="agent-detail__logs">
            {logs.map((entry, i) => (
              <li key={i}>
                <span className="log-entry__badge">
                  {entry.role === "tool" ? (entry.tool_name ?? "tool") : entry.role}
                </span>
                {entry.tools.length > 0 && <span className="log-entry__tools">{entry.tools.join(", ")}</span>}
                {entry.content && <span className="agent-detail__log-text">{entry.content.slice(0, 160)}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
