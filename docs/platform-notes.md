# Platform notes

Conventions this repo follows, and DataHub platform behavior verified against a live v1.6.0 stack during the build (Aug 2026). Kept because most of it was learned the hard way.

## Engineering conventions

1. **Pin everything.** `uv.lock` committed; images pinned (DataHub v1.6.0, MLflow v2.20.3 — the acryl-datahub mlflow connector caps mlflow-skinny <2.21, so the whole stack runs 2.20.x); `acryl-datahub==1.6.0.17`. The repo must run cold on a fresh machine with zero fixes.
2. **The agent is stateless.** No local DB/files as source of truth. Idempotency via evidence hash matched against incidents already in DataHub.
3. **Adapters (`agent/adapters/`) contain zero business logic**, ~50 lines each, mockable. The LLM never calls a raw API.
4. **LLM pluggable**: `LLM_PROVIDER=anthropic|ollama`, temperature 0, prompts as files in `prompts/`, `MAX_TOOL_CALLS=25`. `make demo` must work with Ollama only.
5. **Secrets**: `.env` gitignored; never print tokens. Fix-PR writes only inside `demo/dbt/models/`.
6. **Don't rebuild DataHub features** (impact-analysis UI, Analytics Agent, assertions). Compose and cite; extend the circuit-breaker pattern for the CI gate.
7. Cross-platform: Makefile targets are thin wrappers over POSIX `scripts/*.sh` (bash; runs on macOS/Linux/Git Bash).
8. Keep `make demo` (or the deepest implemented target chain) green at every phase end. `examples/` refreshed automatically by demo runs.

## Verified platform facts (DataHub v1.6.0, Aug 2026)

- DataHub **v1.6.0**; GMS :8080, UI :9002 (datahub/datahub); needs ≥8 GB Docker RAM; secrets ≥32 bytes. Metadata-service auth is opt-in → enabled in our compose; `scripts/bootstrap_token.py` mints the PAT.
- **Incidents**: GraphQL `raiseIncident` / `updateIncident` / `updateIncidentStatus`. On v1.6.0, supported on datasets/dashboards/charts/dataFlow/dataJob — **not mlModel/mlFeature/mlFeatureTable**, which is the limitation that became [RFC datahub#18911](https://github.com/datahub-project/datahub/issues/18911) and has since been fixed upstream (see the Status section in the README). No customProperties on incidents → evidence hash lives in a fenced JSON block inside `description`.
- **No DuckDB connector.** The dbt source (`target_platform: duckdb`) emits dbt nodes + duckdb sibling datasets with column-level lineage and compiled SQL from `manifest.json`+`catalog.json`. Raw tables are dbt *sources*; schema refresh = `dbt docs generate` + re-ingest (this is how break-it lands schema v2). We deliberately dropped the generic-sqlalchemy recipe to keep one URN namespace.
- **URN naming**: duckdb datasets are `warehouse.main.<table>` (db file stem `warehouse`, schema `main`); dbt nodes mirror the same name on platform `dbt`. ML entities: feature table `customer_features` (platform duckdb), features `urn:li:mlFeature:(customer_features,<name>)`, deployment `fraud-scoring-prod` (platform mlflow, env=PROD custom property).
- **MLflow source** maps RegisteredModel→mlModelGroup, ModelVersion→mlModel (name like `fraud_model_1`), run→dataProcessInstance. Lineage is best-effort → `demo/ingest/backfill_ml.py` stitches features/model/deployment unconditionally and read-modify-writes aspects so connector data survives.
- **mcp-server-datahub 0.6.0**: stdio transport, `uvx mcp-server-datahub`, auth via `DATAHUB_GMS_URL`/`DATAHUB_GMS_TOKEN`. Mutation tools behind `TOOLS_IS_MUTATION_ENABLED=true` but documented against DataHub Cloud — **verify against OSS GMS at startup; expect SDK-write fallback to be the real write path.** No incident tools; incidents always via GraphQL.
- **datahub-actions** container runs in quickstart; schema changes = Entity Change Event `category=TECHNICAL_SCHEMA` (Kafka sentinel mode).

## Layout and flow

`make up` → compose (DataHub+MLflow) + token → `make seed` (DuckDB world incl. planted leak `chargebacks_next_30d`) → `make ingest` (dbt build/docs + dbt-source ingestion) → `make train` (sklearn→MLflow `fraud_model`, tags carry `label_column`/`event_ts_column`) → `make backfill` (mlflow ingestion + ML chain stitch + `post-outcome` tag + WATCHLIST) → `make e2e` (assertions in `scripts/e2e_assert.py`) → `make break-it` (poison migration + re-ingest).
