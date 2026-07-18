import { useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";

export function AddAgentView() {
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = () => {
    if (!name.trim() || !objective.trim() || busy) return;
    setBusy(true);
    setStatus(null);
    fetch(`${API_BASE}/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), objective: objective.trim() }),
    })
      .then(async (r) => {
        const payload = await r.json();
        if (r.ok) {
          setStatus(`${payload.role} added — ${payload.note}`);
          setName("");
          setObjective("");
        } else {
          setStatus(`Error: ${payload.error ?? r.statusText}`);
        }
      })
      .catch((e) => setStatus(`Error: ${e}`))
      .finally(() => setBusy(false));
  };

  return (
    <div className="tab-view add-agent">
      <p className="add-agent__hint">
        Register a new agent role. It gets a generated Vibe profile and the full
        blackboard protocol, and joins the team on the next <code>vibe workflow run</code>.
      </p>
      <label className="add-agent__label">
        Role name
        <input
          className="add-agent__input"
          value={name}
          placeholder="Security"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="add-agent__label">
        Objective
        <textarea
          className="add-agent__input add-agent__textarea"
          value={objective}
          placeholder="Audit auth and input validation; message Backend about each finding."
          onChange={(e) => setObjective(e.target.value)}
        />
      </label>
      <button className="add-agent__submit" onClick={submit} disabled={busy}>
        {busy ? "Adding…" : "Add agent"}
      </button>
      {status && <p className="add-agent__status">{status}</p>}
    </div>
  );
}
