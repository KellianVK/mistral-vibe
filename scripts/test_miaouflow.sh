#!/usr/bin/env bash
# MiaouFlow — smoke test du fork (aucun appel LLM, ~1 min, gratuit).
# Usage: ./scripts/test_miaouflow.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PASS=0; FAIL=0

check() { # check <label> <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS  $label"; PASS=$((PASS+1))
  else
    echo "  FAIL  $label"; FAIL=$((FAIL+1))
  fi
}

echo "== 1. Suite de tests offline (70 tests) =="
check "pytest tests/workflow" uv run pytest tests/workflow -q

echo "== 2. CLI native =="
check "vibe workflow --help" uv run vibe workflow --help
check "vibe --help (dispatch intact)" uv run vibe --help
uv run vibe workflow status --workdir /tmp >/dev/null 2>&1
[ $? -eq 1 ] && { echo "  PASS  status sans DB -> erreur propre"; PASS=$((PASS+1)); } \
             || { echo "  FAIL  status sans DB"; FAIL=$((FAIL+1)); }

echo "== 3. Profils générés : restrictions d'outils par rôle =="
T="$(mktemp -d)"
uv run python - <<EOF
from pathlib import Path
from vibe.workflow.roles import select_roles
from vibe.workflow.setup import configure_workdir
configure_workdir(Path("$T"), select_roles(["Planner","Reviewer","Security","Backend"]))
EOF
grep -q 'disabled_tools = \["write_file", "edit", "bash"\]' "$T/.vibe/agents/planner.toml" \
  && { echo "  PASS  Planner sans write/edit/bash"; PASS=$((PASS+1)); } \
  || { echo "  FAIL  restrictions Planner"; FAIL=$((FAIL+1)); }
grep -q 'disabled_tools = \["write_file", "edit"\]' "$T/.vibe/agents/security.toml" \
  && { echo "  PASS  Security sans write/edit"; PASS=$((PASS+1)); } \
  || { echo "  FAIL  restrictions Security"; FAIL=$((FAIL+1)); }
! grep -q 'disabled_tools' "$T/.vibe/agents/backend.toml" \
  && { echo "  PASS  Backend accès complet"; PASS=$((PASS+1)); } \
  || { echo "  FAIL  Backend restreint à tort"; FAIL=$((FAIL+1)); }
grep -q 'miaouflow-reviewer-gate' "$T/.vibe/hooks.toml" \
  && { echo "  PASS  hook reviewer provisionné (équipe avec Reviewer)"; PASS=$((PASS+1)); } \
  || { echo "  FAIL  hooks.toml absent"; FAIL=$((FAIL+1)); }

echo "== 4. Porte de push (hook réel, sans LLM) =="
uv run python - <<EOF
from pathlib import Path
from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import initialize_database, publish_decision
db = workflow_database_path(Path("$T")); initialize_database(db)
publish_decision(db, "Reviewer", "NO-GO: tests rouges", topic="review-verdict")
EOF
OUT=$(echo "{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"git push\"},\"cwd\":\"$T\"}" \
  | uv run python -m vibe.workflow.hooks.guard_push)
echo "$OUT" | grep -q '"deny"' \
  && { echo "  PASS  NO-GO -> push refusé"; PASS=$((PASS+1)); } \
  || { echo "  FAIL  NO-GO non bloqué"; FAIL=$((FAIL+1)); }
uv run python - <<EOF
from pathlib import Path
from vibe.workflow.setup import workflow_database_path
from vibe.workflow.store import publish_decision
publish_decision(workflow_database_path(Path("$T")), "Reviewer", "GO: tout est vert", topic="review-verdict")
EOF
OUT=$(echo "{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"git push\"},\"cwd\":\"$T\"}" \
  | uv run python -m vibe.workflow.hooks.guard_push)
[ -z "$OUT" ] && { echo "  PASS  GO -> push autorisé"; PASS=$((PASS+1)); } \
              || { echo "  FAIL  GO bloqué à tort"; FAIL=$((FAIL+1)); }

echo "== 5. Prompts dédiés + discipline de coût =="
for role in planner backend frontend qa security devops docs reviewer; do
  [ -f "vibe/workflow/prompts/$role.md" ] \
    && { PASS=$((PASS+1)); } || { echo "  FAIL  prompt $role.md manquant"; FAIL=$((FAIL+1)); }
done
echo "  PASS  8 prompts dédiés présents (si aucun FAIL ci-dessus)"

echo "== 6. Board construit et embarqué =="
check "board_dist/index.html présent" test -f vibe/workflow/board_dist/index.html

rm -rf "$T"
echo
echo "Résultat: $PASS PASS, $FAIL FAIL"
[ "$FAIL" -eq 0 ] && echo "SMOKE TEST VERT — prêt pour les scénarios live (voir TESTING.md)"
exit "$FAIL"
