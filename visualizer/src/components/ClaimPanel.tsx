import type { FileClaim } from "../types";

interface ClaimGroup {
  path: string;
  roles: string[];
  conflict: boolean;
}

function groupClaims(claims: FileClaim[]): ClaimGroup[] {
  const byPath = new Map<string, Set<string>>();
  for (const c of claims) {
    if (!byPath.has(c.path)) byPath.set(c.path, new Set());
    byPath.get(c.path)!.add(c.role);
  }
  return [...byPath.entries()].map(([path, roles]) => ({
    path,
    roles: [...roles],
    conflict: roles.size > 1,
  }));
}

export function ClaimPanel({ claims }: { claims: FileClaim[] }) {
  const groups = groupClaims(claims);
  const conflictCount = groups.filter((g) => g.conflict).length;

  return (
    <section className="claim-panel" aria-label="File claims">
      <div className="claim-panel__head">
        <h2 className="panel-section__title">File Claims</h2>
        {conflictCount > 0 && (
          <span className="claim-panel__conflict-badge">
            {conflictCount} conflict{conflictCount > 1 ? "s" : ""}
          </span>
        )}
      </div>
      {groups.length === 0 ? (
        <p className="panel-section__empty panel-section__empty--inline">No active claims.</p>
      ) : (
        <ul className="claim-list">
          {groups.map((g) => (
            <li key={g.path} className={g.conflict ? "claim-list__item claim-list__item--conflict" : "claim-list__item"}>
              <span className="claim-list__path">{g.path}</span>
              <span className="claim-list__roles">{g.roles.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
