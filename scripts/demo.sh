#!/usr/bin/env bash
# The golden path, unattended: break production -> the agent detects, walks
# the blast radius, diagnoses, acts (incident + tag + doc + alert + fix PR),
# proves idempotency, audits for target leakage, and (with a runner online)
# shows the deployment gate blocking. Leaves the incident active on purpose —
# that's the demo's end state; `make resolve && make seed ingest` restores.
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"
curl -sf "${DATAHUB_GMS_URL}/health" >/dev/null || die "DataHub is not up — run 'make up'"

RAW_URN='urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)'

say "1/4 · breaking production (amount_usd -> amount, dollars -> cents; nothing crashes)"
bash scripts/break-it.sh >/dev/null

say "2/4 · the agent runs: detect -> traverse -> diagnose -> act -> remember"
uv run blast-radius scan "$RAW_URN"

say "3/4 · run it again — the graph remembers"
uv run blast-radius scan "$RAW_URN"

say "4/4 · leakage audit: does any feature contain the answer?"
uv run blast-radius audit

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 \
   && [ "$(gh api repos/{owner}/{repo}/actions/runners -q '[.runners[] | select(.status == "online")] | length' 2>/dev/null)" -ge 1 ] 2>/dev/null; then
  say "bonus · try to deploy anyway: the gate asks DataHub first"
  bash scripts/e2e_gate.sh red || true
fi

say "demo complete"
echo "  incident + evidence : http://localhost:9002 (datahub/datahub)"
echo "  artifacts           : examples/ (evidence json, leakage report, transcript)"
echo "  restore             : make resolve && make seed && make ingest"
