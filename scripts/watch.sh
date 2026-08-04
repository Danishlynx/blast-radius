#!/usr/bin/env bash
# Run the sentinel daemon (schema-change poller over the WATCHLIST).
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"
uv run blast-radius watch
