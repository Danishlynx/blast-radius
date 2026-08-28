#!/usr/bin/env bash
# One-shot scan: diff schema history for a dataset URN and run the pipeline.
. "$(dirname "$0")/lib.sh"

[ -n "${1:-}" ] || die "usage: make scan URN=<dataset urn>"
[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"
uv run blast-radius scan "$1"
