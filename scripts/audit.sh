#!/usr/bin/env bash
# Run the structural target-leakage audit against the demo model.
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"
uv run blast-radius audit "$@"
