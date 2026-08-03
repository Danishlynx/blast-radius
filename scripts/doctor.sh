#!/usr/bin/env bash
# Environment diagnostics. Prints actionable findings; exits non-zero if any
# hard requirement is missing.
. "$(dirname "$0")/lib.sh"
set +e

FAILURES=0
req() { # req <label> <check-command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else warn "$label — MISSING"; FAILURES=$((FAILURES+1)); fi
}

say "checking host prerequisites"
req "docker CLI present" command -v docker
req "docker engine running" docker info
req "docker compose v2" docker compose version
req "uv present" command -v uv
req "make present" command -v make
req "curl present" command -v curl
req "git present" command -v git

say "checking docker resources"
MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
if [ "${MEM_BYTES:-0}" -ge 7000000000 ] 2>/dev/null; then
  ok "docker memory: $((MEM_BYTES / 1073741824)) GiB (need >= 8 GiB for DataHub)"
else
  warn "docker memory looks low (${MEM_BYTES} bytes) — DataHub quickstart needs ~8 GiB"
  FAILURES=$((FAILURES+1))
fi

say "checking stack (informational)"
if curl -sf -o /dev/null "${DATAHUB_GMS_URL}/health"; then
  ok "DataHub GMS healthy at ${DATAHUB_GMS_URL}"
  if [ -n "${DATAHUB_TOKEN:-}" ]; then
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${DATAHUB_TOKEN}" \
      -H 'Content-Type: application/json' -X POST "${DATAHUB_GMS_URL}/api/graphql" \
      -d '{"query":"{ me { corpUser { username } } }"}')
    if [ "$CODE" = "200" ]; then ok "DATAHUB_TOKEN valid"; else warn "DATAHUB_TOKEN present but rejected (HTTP $CODE) — rerun 'make up' to re-mint"; fi
  else
    warn "no DATAHUB_TOKEN in .env yet — 'make up' mints one"
  fi
else
  warn "DataHub GMS not reachable at ${DATAHUB_GMS_URL} — run 'make up'"
fi
if curl -sf -o /dev/null "${MLFLOW_TRACKING_URI}/health"; then
  ok "MLflow healthy at ${MLFLOW_TRACKING_URI}"
else
  warn "MLflow not reachable at ${MLFLOW_TRACKING_URI} — run 'make up'"
fi

if [ "$FAILURES" -gt 0 ]; then
  die "$FAILURES problem(s) found"
fi
say "all checks passed"
