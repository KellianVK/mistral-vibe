#!/usr/bin/env bash
# MiaouFlow golden-path rehearsal: two consecutive real runs of the demo
# scenario, exactly as it will be shown live. Uses the local fork's vibe and
# the auth already configured in ~/.vibe.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
GOAL="Build a small Todo API with JWT auth: Flask app in server/app.py with POST /auth/register and POST /auth/login returning {token, expires_in}, and /todos CRUD guarded by the JWT. Frontend: a minimal web/index.html client for login and listing/adding todos against the published contract. Keep everything small and runnable."
SCRATCH="$(mktemp -d /tmp/miaou-rehearsal-XXXXXX)"
TIMEOUT="${MIAOU_TIMEOUT:-600}"

echo "Rehearsal scratch: $SCRATCH"
for run in 1 2; do
  DEST="$SCRATCH/run$run"
  cp -R "$REPO/demo-project" "$DEST"
  echo "=== Rehearsal run $run/2 (timeout ${TIMEOUT}s) ==="
  (cd "$REPO" && uv run python -m vibe workflow run \
    --goal "$GOAL" --workdir "$DEST" --no-board --timeout "$TIMEOUT")

  DB="$DEST/.vibe/workflow.db"
  DECISIONS=$(sqlite3 "$DB" "select count(*) from decisions")
  BLOCKED=$(sqlite3 "$DB" "select count(*) from status where state != 'done'")
  echo "run $run: $DECISIONS decisions, $BLOCKED non-done roles"
  [ "$DECISIONS" -gt 0 ] || { echo "FAIL: no decisions published"; exit 1; }
  [ "$BLOCKED" -eq 0 ] || { echo "FAIL: some roles did not finish"; exit 1; }
  echo "=== run $run OK ==="
done

echo "Rehearsal complete: two consecutive green runs. Demo command:"
echo "  vibe workflow run --goal \"$GOAL\" --workdir demo-project"
