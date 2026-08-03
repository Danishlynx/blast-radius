#!/usr/bin/env bash
# Acceptance tests. Runs the assertions implemented so far; grows through the
# build (see handoff §9). Requires a running, ingested stack.
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"

uv run python scripts/e2e_assert.py
