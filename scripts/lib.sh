#!/usr/bin/env bash
# Shared helpers for all Blast Radius scripts. POSIX-leaning bash; runs on
# macOS, Linux, and Windows Git Bash.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env if present (exported so child processes see it).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

DATAHUB_GMS_URL="${DATAHUB_GMS_URL:-http://localhost:8080}"
MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-http://localhost:5000}"

# Windows consoles default Python to cp1252, which chokes on emoji in
# third-party log output (e.g. MLflow). Force UTF-8 everywhere.
export PYTHONUTF8=1

say()  { printf '\033[1;36m[blast-radius]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# wait_for <name> <url> <timeout_seconds>
wait_for() {
  local name="$1" url="$2" timeout="${3:-180}" waited=0
  say "waiting for ${name} (${url}) ..."
  until curl -sf -o /dev/null "$url"; do
    sleep 3
    waited=$((waited + 3))
    if [ "$waited" -ge "$timeout" ]; then
      die "${name} not healthy after ${timeout}s — try 'make doctor'"
    fi
  done
  ok "${name} healthy"
}
