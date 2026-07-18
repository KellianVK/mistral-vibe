import { useState } from "react";
import { API_BASE } from "../hooks/useWorkflowState";
import type { FileChange } from "../types";

const ACTION_ICON: Record<FileChange["action"], string> = {
  created: "+",
  modified: "~",
  deleted: "−",
};

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="diff-block">
      {diff.split("\n").map((line, i) => {
        let cls = "diff-line";
        if (line.startsWith("+++") || line.startsWith("---")) cls += " diff-line--file";
        else if (line.startsWith("@@")) cls += " diff-line--hunk";
        else if (line.startsWith("+")) cls += " diff-line--add";
        else if (line.startsWith("-")) cls += " diff-line--del";
        return (
          <span key={i} className={cls}>
            {line || " "}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export function ChangesView({ changes }: { changes: FileChange[] }) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [diffs, setDiffs] = useState<Record<number, string | null>>({});
  const recent = [...changes].reverse();

  const toggle = (change: FileChange) => {
    if (openId === change.id) {
      setOpenId(null);
      return;
    }
    setOpenId(change.id);
    if (!(change.id in diffs)) {
      fetch(`${API_BASE}/change?id=${change.id}`)
        .then((r) => r.json())
        .then((payload) => setDiffs((d) => ({ ...d, [change.id]: payload.diff ?? null })))
        .catch(() => setDiffs((d) => ({ ...d, [change.id]: null })));
    }
  };

  return (
    <div className="tab-view">
      {recent.length === 0 ? (
        <div className="empty-state">No file changes recorded yet — they appear as agents write code.</div>
      ) : (
        <ul className="change-list">
          {recent.map((change) => (
            <li key={change.id} className="change-item">
              <button
                className={`change-row change-row--${change.action}${openId === change.id ? " change-row--open" : ""}`}
                onClick={() => toggle(change)}
              >
                <span className="change-row__action">{ACTION_ICON[change.action]}</span>
                <code className="change-row__path">{change.path}</code>
                <span className="change-row__meta">
                  <span className="change-row__role">{change.role}</span>
                  <span className="change-row__chevron" aria-hidden="true">
                    {openId === change.id ? "▾" : "▸"}
                  </span>
                </span>
              </button>
              {openId === change.id &&
                (diffs[change.id] === undefined ? (
                  <p className="panel-section__empty change-item__detail">Loading diff…</p>
                ) : diffs[change.id] ? (
                  <div className="change-item__detail">
                    <DiffBlock diff={diffs[change.id] as string} />
                  </div>
                ) : (
                  <p className="panel-section__empty change-item__detail">
                    No text diff available (binary or large file).
                  </p>
                ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
