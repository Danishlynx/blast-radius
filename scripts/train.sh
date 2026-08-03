#!/usr/bin/env bash
# Train the fraud model and register it in MLflow.
. "$(dirname "$0")/lib.sh"

say "training fraud_model"
uv run python demo/train.py
