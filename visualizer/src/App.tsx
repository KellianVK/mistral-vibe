import { useState } from "react";
import { AddAgentView } from "./components/AddAgentView";
import { ChangesView } from "./components/ChangesView";
import { ClaimPanel } from "./components/ClaimPanel";
import { DecisionFeed } from "./components/DecisionFeed";
import { LogsView } from "./components/LogsView";
import { MessagesPanel } from "./components/MessagesPanel";
import { OpenQuestions } from "./components/OpenQuestions";
import { SystemView } from "./components/SystemView";
import { TeamCanvas } from "./components/TeamCanvas";
import { WorkflowHeader } from "./components/WorkflowHeader";
import { useWorkflowState } from "./hooks/useWorkflowState";
import "./theme.css";
import "./App.css";

const TABS = ["Board", "Logs", "Changes", "Add agent", "System"] as const;
type Tab = (typeof TABS)[number];

function App() {
  const { manifest, state, connection } = useWorkflowState();
  const [tab, setTab] = useState<Tab>("Board");

  return (
    <div className="app">
      <WorkflowHeader manifest={manifest} state={state} connection={connection} />

      <nav className="tab-bar">
        {TABS.map((t) => (
          <button
            key={t}
            className={`tab-bar__tab${t === tab ? " tab-bar__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>

      {tab === "Board" && (
        <>
          <div className="app-body">
            <div className="app-body__main">
              {manifest.roles.length === 0 ? (
                <div className="empty-state">
                  No workflow yet — run{" "}
                  <code>vibe workflow run --goal "..."</code> in the target project.
                </div>
              ) : (
                <TeamCanvas manifest={manifest} state={state} />
              )}
            </div>

            <aside className="app-body__rail">
              <DecisionFeed decisions={state.decisions} />
              <MessagesPanel
                messages={state.messages ?? []}
                broadcasts={state.broadcasts ?? []}
              />
              <OpenQuestions questions={state.questions} />
            </aside>
          </div>

          <ClaimPanel claims={state.claims} />
        </>
      )}

      {tab === "Logs" && <LogsView manifest={manifest} />}
      {tab === "Changes" && <ChangesView changes={state.changes ?? []} />}
      {tab === "Add agent" && <AddAgentView />}
      {tab === "System" && <SystemView />}
    </div>
  );
}

export default App;
