import { useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";
import type { Manifest } from "../types";

const MODELS = [
  { id: "devstral-small", label: "Devstral (coding)" },
  { id: "mistral-medium-3.5", label: "Mistral Medium 3.5 (reasoning)" },
];

const SUGGESTIONS = [
  { name: "Security", objective: "Audit auth and input validation; message Backend about each finding." },
  { name: "Docs", objective: "Write the README from the published decisions and delivered files." },
  { name: "DevOps", objective: "Prepare the run command and a minimal CI config." },
];

export function AddAgentView({ manifest }: { manifest: Manifest }) {
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [model, setModel] = useState(MODELS[0].id);
  const [deps, setDeps] = useState<string[]>(["Planner"]);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const existingRoles = manifest.roles.map((r) => r.name);
  const depOptions = existingRoles.length > 0 ? existingRoles : ["Planner", "Backend", "Frontend"];

  const toggleDep = (role: string) =>
    setDeps((d) => (d.includes(role) ? d.filter((x) => x !== role) : [...d, role]));

  const submit = () => {
    if (!name.trim() || !objective.trim() || busy) return;
    setBusy(true);
    setStatus(null);
    fetch(`${API_BASE}/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name.trim(),
        objective: objective.trim(),
        model,
        depends_on: deps,
      }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (r.ok) {
          setStatus({ ok: true, text: `${payload.role} added — ${payload.note}` });
          setName("");
          setObjective("");
        } else {
          setStatus({ ok: false, text: payload.error ?? r.statusText });
        }
      })
      .catch((e) => setStatus({ ok: false, text: String(e) }))
      .finally(() => setBusy(false));
  };

  return (
    <div className="tab-view add-agent-wrap">
      <div className="add-agent-card">
        <div className="add-agent-card__head">
          <h2 className="add-agent-card__title">Add an agent</h2>
          <p className="add-agent-card__hint">
            The new role gets a generated Vibe profile and all 11 blackboard tools, and joins the team
            on the next run.
          </p>
        </div>

        <div className="add-agent-card__suggestions">
          {SUGGESTIONS.filter((s) => !existingRoles.includes(s.name)).map((s) => (
            <button
              key={s.name}
              className="suggestion-chip"
              onClick={() => {
                setName(s.name);
                setObjective(s.objective);
              }}
            >
              {s.name}
            </button>
          ))}
        </div>

        <label className="field">
          <span className="field__label">Role name</span>
          <input
            className="field__input"
            value={name}
            placeholder="Security"
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <label className="field">
          <span className="field__label">Objective</span>
          <textarea
            className="field__input field__input--textarea"
            value={objective}
            placeholder="Audit auth and input validation; message Backend about each finding."
            onChange={(e) => setObjective(e.target.value)}
          />
        </label>

        <div className="field-row">
          <label className="field field--grow">
            <span className="field__label">Model</span>
            <select className="field__input" value={model} onChange={(e) => setModel(e.target.value)}>
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="field">
          <span className="field__label">Runs after</span>
          <div className="dep-chips">
            {depOptions.map((role) => (
              <button
                key={role}
                className={`dep-chip${deps.includes(role) ? " dep-chip--on" : ""}`}
                onClick={() => toggleDep(role)}
              >
                {role}
              </button>
            ))}
          </div>
        </div>

        <div className="add-agent-card__foot">
          <button
            className="add-agent__submit"
            onClick={submit}
            disabled={busy || !name.trim() || !objective.trim()}
          >
            {busy ? "Adding…" : "Add agent"}
          </button>
          {status && (
            <p className={`add-agent__status${status.ok ? "" : " add-agent__status--error"}`}>
              {status.text}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
