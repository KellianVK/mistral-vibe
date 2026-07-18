import { ClaimPanel } from "./components/ClaimPanel";
import { DecisionFeed } from "./components/DecisionFeed";
import { OpenQuestions } from "./components/OpenQuestions";
import { TeamCanvas } from "./components/TeamCanvas";
import { WorkflowHeader } from "./components/WorkflowHeader";
import { useWorkflowState } from "./hooks/useWorkflowState";
import "./theme.css";
import "./App.css";

function App() {
  const { manifest, state, connection } = useWorkflowState();

  return (
    <div className="app">
      <WorkflowHeader manifest={manifest} state={state} connection={connection} />

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
          <OpenQuestions questions={state.questions} />
        </aside>
      </div>

      <ClaimPanel claims={state.claims} />
    </div>
  );
}

export default App;
