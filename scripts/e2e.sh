#!/usr/bin/env bash
# Acceptance tests (handoff §9). Requires a running, ingested stack in the
# HEALTHY state; leaves it healthy again. Covers:
#   1  the world exists with column-level lineage
#   2  break -> one agent run files exactly 1 incident + tag + run log
#   3  re-run is idempotent ("duplicate suppressed", still exactly 1 incident)
#   -  resolve clears the incident + tag; world restored; assertion 1 again
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"

RAW_URN='urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)'

say "assertion 1: the world"
uv run python scripts/e2e_assert.py world

say "assertion 2: break -> detect -> act"
bash scripts/break-it.sh >/dev/null
uv run blast-radius scan "$RAW_URN" > /tmp/br-scan1.log 2>/dev/null || { cat /tmp/br-scan1.log; die "scan failed"; }
grep -q "incident" /tmp/br-scan1.log || die "scan did not act"
uv run python scripts/e2e_assert.py incidents-active

say "assertion 3: idempotent re-run"
uv run blast-radius scan "$RAW_URN" > /tmp/br-scan2.log 2>/dev/null || { cat /tmp/br-scan2.log; die "re-scan failed"; }
grep -q "duplicate suppressed" /tmp/br-scan2.log || die "no 'duplicate suppressed' on re-run"
ok "duplicate suppressed on re-run"
uv run python scripts/e2e_assert.py incidents-active

# assertion 5 (gate) runs only when a self-hosted runner is online; judges
# without one still complete the suite.
GATE=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 \
   && [ "$(gh api repos/{owner}/{repo}/actions/runners -q '[.runners[] | select(.status == "online")] | length' 2>/dev/null)" -ge 1 ] 2>/dev/null; then
  GATE=1
  say "assertion 5a: deployment gate blocks while the incident is active"
  bash scripts/e2e_gate.sh red
else
  warn "no online self-hosted runner — skipping gate assertions (5a/5b)"
fi

say "resolve + restore"
uv run python demo/resolve.py
uv run python scripts/e2e_assert.py incidents-clear
if [ "$GATE" = "1" ]; then
  say "assertion 5b: deployment gate passes after resolve"
  bash scripts/e2e_gate.sh green
fi
bash scripts/seed.sh >/dev/null
bash scripts/ingest.sh >/dev/null
uv run python scripts/e2e_assert.py world

say "all acceptance assertions passed"
