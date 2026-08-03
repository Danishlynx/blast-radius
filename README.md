# Blast Radius

> An autonomous agent that guards the data supply chain of production ML models — it finds every model in the blast radius of an upstream change, proves it with evidence, blocks bad deploys, drafts the fix, and files everything back into DataHub.

**Status: under construction** (hackathon build in progress — DataHub Agent Hackathon, Challenge 3: Production ML Agents).

## Quickstart

Prerequisites: Docker (≥8 GB RAM allocated), [uv](https://docs.astral.sh/uv/), GNU make, Python 3.11+.

```bash
git clone <this repo> && cd blast-radius
make up        # DataHub v1.6.0 + MLflow, waits healthy, mints a token into .env
make seed      # synthetic fintech warehouse (DuckDB)
make ingest    # dbt build + column-level lineage into DataHub
make train     # sklearn fraud model, registered in MLflow
make backfill  # stitch the full ML lineage chain in DataHub
make e2e       # acceptance assertions
```

Then break production and watch the agent catch it:

```bash
make break-it  # amount_usd -> amount, dollars -> cents. Nothing crashes.
```

- DataHub UI: http://localhost:9002 (datahub / datahub)
- MLflow UI: http://localhost:5000

## What exists so far

- Docker compose stack: DataHub v1.6.0 (auth enabled) + MLflow 2.20.3
- Synthetic fintech world with a **planted target-leakage feature** (`chargebacks_next_30d` — aggregates chargebacks from *after* the prediction timestamp)
- End-to-end lineage in DataHub: `raw_transactions → fct_customer_features → mlFeatures → fraud_model → fraud-scoring-prod`, with column-level lineage and captured SQL
- The poison migration (`make break-it`)

The agent (detect → traverse → diagnose → act → remember), the leakage auditor, and the CI deployment gate are landing next.

## License

Apache-2.0
