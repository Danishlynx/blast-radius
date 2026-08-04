#!/usr/bin/env bash
# Resolve Blast Radius incidents + clear the model-at-risk tag
# (the gate-green demo beat).
. "$(dirname "$0")/lib.sh"

[ -n "${DATAHUB_TOKEN:-}" ] || die "no DATAHUB_TOKEN in .env — run 'make up' first"
uv run python demo/resolve.py
