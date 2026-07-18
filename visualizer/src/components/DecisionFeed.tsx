import type { Decision } from "../types";

function formatTime(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function DecisionFeed({ decisions }: { decisions: Decision[] }) {
  const chronological = [...decisions].reverse();

  return (
    <section className="panel-section" aria-label="Decision feed">
      <h2 className="panel-section__title">Decisions</h2>
      {chronological.length === 0 ? (
        <p className="panel-section__empty">No decisions yet.</p>
      ) : (
        <ul className="feed">
          {chronological.map((d, i) => (
            <li key={i} className="feed__item">
              <div className="feed__meta">
                <span className="feed__role">{d.role}</span>
                <span className="feed__time">{formatTime(d.ts)}</span>
              </div>
              <p className="feed__text">{d.summary}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
