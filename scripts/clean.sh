#!/usr/bin/env bash
# Tear down the docker stack (volumes included) and delete generated demo
# artifacts. The repo returns to a pre-`make up` state.
. "$(dirname "$0")/lib.sh"

say "stopping stack and removing volumes"
docker compose down -v --remove-orphans || true

say "removing generated artifacts"
rm -f demo/warehouse.duckdb demo/warehouse.duckdb.wal
rm -rf demo/dbt/target demo/dbt/logs mlruns mlartifacts

ok "clean"
