#!/usr/bin/env bash
# Ingest MLflow metadata, then stitch the complete ML lineage chain and apply
# the post-outcome tag. Also seeds WATCHLIST in .env.
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"

say "ingesting MLflow metadata into DataHub"
uv run datahub ingest -c demo/ingest/mlflow.yml

say "backfilling ML lineage chain"
uv run python demo/ingest/backfill_ml.py

say "tagging post-outcome assets"
uv run python demo/ingest/tag_post_outcome.py

RAW_URN='urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)'
# single-quoted in .env: URNs contain parentheses, which break unquoted sourcing
if grep -q '^WATCHLIST=' .env; then
  # portable in-place edit (BSD/GNU sed differ; write a temp file instead)
  awk -v urn="$RAW_URN" '/^WATCHLIST=/{print "WATCHLIST=\x27" urn "\x27"; next} {print}' .env > .env.tmp && mv .env.tmp .env
else
  printf "WATCHLIST='%s'\n" "$RAW_URN" >> .env
fi
ok "WATCHLIST set to $RAW_URN"
