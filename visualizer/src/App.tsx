import { AgentGraph } from "./components/AgentGraph";
import { useWorkflowState } from "./hooks/useWorkflowState";
import "./App.css";

function App() {
  const { manifest, state, connected } = useWorkflowState();
  const openQuestions = state.questions.filter((q) => !q.resolved);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>mistral workflow</h1>
          <p className="goal">{manifest.project?.goal ?? "Waiting for a project…"}</p>
        </div>
        <div className={connected ? "conn-badge conn-badge--live" : "conn-badge conn-badge--poll"}>
          {connected ? "live" : "polling"}
        </div>
      </header>

      <div className="app-body">
        <div className="graph-pane">
          {manifest.roles.length === 0 ? (
            <div className="empty-state">
              No workflow yet — run <code>mistral workflow init</code> then{" "}
              <code>mistral workflow run</code> in the target project.
            </div>
          ) : (
            <AgentGraph manifest={manifest} agents={state.agents} />
          )}
        </div>

        <aside className="side-panel">
          <section>
            <h2>Decisions</h2>
            <ul className="feed">
              {state.decisions.length === 0 && <li className="feed__empty">No decisions yet.</li>}
              {[...state.decisions].reverse().map((d, i) => (
                <li key={i}>
                  <span className="role-tag">{d.role}</span>
                  <span>{d.summary}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2>Questions</h2>
            <ul className="feed">
              {openQuestions.length === 0 && <li className="feed__empty">No open questions.</li>}
              {openQuestions.map((q, i) => (
                <li key={i}>
                  <span className="role-tag">
                    {q.from} → {q.to}
                  </span>
                  <span>{q.question}</span>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default App;
