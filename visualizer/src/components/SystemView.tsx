import { useEffect, useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";

interface SystemInfo {
  database: string;
  agent_profiles: { name: string; content: string }[];
  blackboard_tools: { name: string; description: string }[];
  role_prompts: string[];
}

export function SystemView() {
  const [info, setInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/system`)
      .then((r) => r.json())
      .then(setInfo)
      .catch(() => {});
  }, []);

  if (!info) return <p className="panel-section__empty">Loading system info…</p>;

  return (
    <div className="tab-view system-view">
      <section className="panel-section">
        <h2 className="panel-section__title">Blackboard</h2>
        <code className="system-view__db">{info.database}</code>
      </section>
      <section className="panel-section">
        <h2 className="panel-section__title">Native tools ({info.blackboard_tools.length})</h2>
        <ul className="system-view__tools">
          {info.blackboard_tools.map((tool) => (
            <li key={tool.name}>
              <code>{tool.name}</code> — {tool.description}
            </li>
          ))}
        </ul>
      </section>
      <section className="panel-section">
        <h2 className="panel-section__title">Agent profiles</h2>
        {info.agent_profiles.length === 0 ? (
          <p className="panel-section__empty">None provisioned yet — run a workflow.</p>
        ) : (
          info.agent_profiles.map((profile) => (
            <details key={profile.name} className="system-view__profile">
              <summary>{profile.name}.toml</summary>
              <pre>{profile.content}</pre>
            </details>
          ))
        )}
      </section>
      <section className="panel-section">
        <h2 className="panel-section__title">Role prompts</h2>
        <p className="system-view__prompts">{info.role_prompts.join(" · ")}</p>
      </section>
    </div>
  );
}
