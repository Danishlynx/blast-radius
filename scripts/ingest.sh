#!/usr/bin/env bash
# Build the dbt project, generate docs artifacts, and ingest dbt metadata
# (models + sources + column-level lineage + compiled SQL) into DataHub.
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"

say "dbt build"
uv run dbt build --project-dir demo/dbt --profiles-dir demo/dbt

say "dbt docs generate (manifest + catalog for DataHub)"
uv run dbt docs generate --project-dir demo/dbt --profiles-dir demo/dbt

say "ingesting dbt metadata into DataHub"
uv run datahub ingest -c demo/ingest/dbt.yml

say "ingest complete — browse http://localhost:9002"
