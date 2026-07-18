import type { TeamBroadcast, TeamMessage } from "../types";

function formatTime(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function MessagesPanel({
  messages,
  broadcasts,
}: {
  messages: TeamMessage[];
  broadcasts: TeamBroadcast[];
}) {
  const merged = [
    ...messages.map((m) => ({ key: `m${m.id}`, ts: m.ts, from: m.from, to: m.to, content: m.content, unread: !m.read })),
    ...broadcasts.map((b) => ({ key: `b${b.id}`, ts: b.ts, from: b.from, to: "team", content: b.content, unread: false })),
  ]
    .sort((a, b) => (a.ts < b.ts ? 1 : -1))
    .slice(0, 12);

  return (
    <section className="panel-section" aria-label="Messages">
      <h2 className="panel-section__title">Messages</h2>
      {merged.length === 0 ? (
        <p className="panel-section__empty">No messages yet.</p>
      ) : (
        <ul className="feed">
          {merged.map((m) => (
            <li key={m.key} className={`feed__item${m.unread ? " feed__item--unread" : ""}`}>
              <div className="feed__meta">
                <span className="feed__role">
                  {m.from} → {m.to}
                </span>
                <span className="feed__time">{formatTime(m.ts)}</span>
              </div>
              <p className="feed__text">{m.content}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
