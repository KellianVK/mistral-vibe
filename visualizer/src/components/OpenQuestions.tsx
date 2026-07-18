import type { Question } from "../types";

export function OpenQuestions({ questions }: { questions: Question[] }) {
  const open = questions.filter((q) => !q.resolved);

  return (
    <section className="panel-section" aria-label="Open questions">
      <h2 className="panel-section__title">Open Questions</h2>
      {open.length === 0 ? (
        <p className="panel-section__empty">No open questions.</p>
      ) : (
        <ul className="feed">
          {open.map((q, i) => (
            <li key={i} className="feed__item feed__item--question">
              <div className="feed__meta">
                <span className="feed__role">
                  {q.from} → {q.to}
                </span>
              </div>
              <p className="feed__text">{q.question}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
