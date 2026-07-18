import type { FileChange } from "../types";

const ACTION_ICON: Record<FileChange["action"], string> = {
  created: "+",
  modified: "~",
  deleted: "−",
};

export function ChangesView({ changes }: { changes: FileChange[] }) {
  const recent = [...changes].reverse();
  return (
    <div className="tab-view">
      {recent.length === 0 ? (
        <p className="panel-section__empty">No file changes recorded yet.</p>
      ) : (
        <ul className="change-list">
          {recent.map((change) => (
            <li key={change.id} className={`change-row change-row--${change.action}`}>
              <span className="change-row__action">{ACTION_ICON[change.action]}</span>
              <code className="change-row__path">{change.path}</code>
              <span className="change-row__role">{change.role}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
