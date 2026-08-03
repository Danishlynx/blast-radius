#!/usr/bin/env bash
# Generate the synthetic fintech DuckDB warehouse.
. "$(dirname "$0")/lib.sh"

say "seeding synthetic warehouse"
uv run python demo/seed/generate.py
