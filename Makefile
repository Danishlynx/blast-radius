# Blast Radius — all targets are thin wrappers over POSIX scripts in scripts/
# so the same code path runs on macOS, Linux, and Windows (Git Bash).
SHELL := bash

.PHONY: up seed ingest train backfill demo break-it resolve audit e2e doctor clean scan watch

up: ## Start DataHub + MLflow stack, wait healthy, mint a token into .env
	bash scripts/up.sh

seed: ## Generate the synthetic fintech DuckDB warehouse
	bash scripts/seed.sh

ingest: ## dbt build + docs generate, then ingest dbt + MLflow metadata into DataHub
	bash scripts/ingest.sh

train: ## Train the fraud model and register it in MLflow
	bash scripts/train.sh

backfill: ## Stitch the full ML lineage chain in DataHub (features, model, deployment)
	bash scripts/backfill.sh

break-it: ## Apply the poison migration (amount_usd -> amount, dollars -> cents) and re-ingest
	bash scripts/break-it.sh

demo: ## Full golden path: break -> detect -> act -> gate (unattended)
	bash scripts/demo.sh

resolve: ## Resolve the demo incident (for the gate-green demo beat)
	bash scripts/resolve.sh

audit: ## Run the target-leakage audit against the fraud model
	bash scripts/audit.sh

e2e: ## Acceptance assertions (scripts/e2e.sh)
	bash scripts/e2e.sh

doctor: ## Diagnose environment problems
	bash scripts/doctor.sh

scan: ## Manual change scan: make scan URN=<dataset urn>
	bash scripts/scan.sh $(URN)

watch: ## Run the sentinel daemon
	bash scripts/watch.sh

clean: ## Tear down the stack and delete generated demo artifacts
	bash scripts/clean.sh
