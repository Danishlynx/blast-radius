#!/usr/bin/env bash
# Assertion 5 helper: dispatch deploy-model.yml and assert the gate verdict.
# Usage: e2e_gate.sh red|green
# Requires: gh CLI authenticated + an online self-hosted runner. Callers
# should skip gracefully when those aren't available (see e2e.sh).
. "$(dirname "$0")/lib.sh"

EXPECT="${1:?usage: e2e_gate.sh red|green}"

say "dispatching deploy-model.yml (expecting gate ${EXPECT})"
gh workflow run deploy-model.yml >/dev/null

# Wait for the new run to register, then for completion.
sleep 8
RUN_ID=$(gh run list --workflow=deploy-model.yml --limit 1 --json databaseId -q '.[0].databaseId')
[ -n "$RUN_ID" ] || die "no workflow run found"
say "waiting for run $RUN_ID ..."
gh run watch "$RUN_ID" --exit-status >/dev/null 2>&1 || true  # non-zero = run failed, which may be the expected verdict
CONCLUSION=$(gh run view "$RUN_ID" --json conclusion -q .conclusion)

if [ "$EXPECT" = "red" ] && [ "$CONCLUSION" = "failure" ]; then
  ok "gate RED as expected (deploy blocked while incident active)"
elif [ "$EXPECT" = "green" ] && [ "$CONCLUSION" = "success" ]; then
  ok "gate GREEN as expected (supply chain healthy)"
else
  die "expected gate ${EXPECT}, run $RUN_ID concluded: ${CONCLUSION}"
fi
