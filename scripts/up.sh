#!/usr/bin/env bash
# Bring up the DataHub + MLflow demo stack, wait for health, and make sure
# .env has a working DataHub personal access token.
. "$(dirname "$0")/lib.sh"

say "starting stack (docker compose up -d)"
docker compose up -d --wait --wait-timeout 600 || {
  warn "compose --wait reported failure; checking service health directly"
}

wait_for "DataHub GMS" "${DATAHUB_GMS_URL}/health" 300
wait_for "DataHub UI" "http://localhost:9002/admin" 300
wait_for "MLflow" "${MLFLOW_TRACKING_URI}/health" 300

if [ ! -f .env ]; then
  say "creating .env from .env.example"
  cp .env.example .env
fi

say "ensuring DataHub access token"
uv run python scripts/bootstrap_token.py

say "stack is up — UI: http://localhost:9002 (datahub/datahub) · GMS: ${DATAHUB_GMS_URL} · MLflow: ${MLFLOW_TRACKING_URI}"
