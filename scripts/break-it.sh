#!/usr/bin/env bash
# Apply the poison migration (amount_usd -> amount, dollars -> cents), then
# re-generate dbt artifacts and re-ingest so DataHub registers schema v2.
. "$(dirname "$0")/lib.sh"

say "applying poison migration (demo/break_it.sql)"
uv run python - <<'PY'
from pathlib import Path
import duckdb

sql = Path("demo/break_it.sql").read_text()
con = duckdb.connect("demo/warehouse.duckdb")
con.execute(sql)
cols = [r[0] for r in con.execute("describe raw_transactions").fetchall()]
con.close()
print(f"raw_transactions columns are now: {cols}")
PY

say "re-generating dbt artifacts against the new schema"
# compile-only: the broken models must NOT be rebuilt — in the real incident
# nothing reruns; docs generate re-introspects the warehouse for catalog.json.
uv run dbt docs generate --project-dir demo/dbt --profiles-dir demo/dbt --no-compile || \
  uv run dbt docs generate --project-dir demo/dbt --profiles-dir demo/dbt

say "re-ingesting into DataHub (schema v2 lands)"
uv run datahub ingest -c demo/ingest/dbt.yml

say "the trap is set — raw_transactions now has 'amount' in cents; every dbt model still references amount_usd"
