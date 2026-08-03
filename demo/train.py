"""Train the fraud model on fct_customer_features and register it in MLflow.

Time-ordered split (no shuffle leakage across time), sklearn gradient
boosting, logged with signature + metrics. The label and event-timestamp
column names are attached as registered-model tags so downstream tooling
(the leakage auditor) can discover them from metadata alone.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import mlflow
import pandas as pd
from mlflow.models import infer_signature
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

DB_PATH = Path(__file__).resolve().parent / "warehouse.duckdb"
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

MODEL_NAME = "fraud_model"
LABEL_COLUMN = "is_fraud"
EVENT_TS_COLUMN = "event_ts"
FEATURES = [
    "txn_count_30d",
    "avg_amount_30d",
    "distinct_merchants_30d",
    "country_risk",
    "chargebacks_next_30d",
]


def load_features() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute(
            "select * from fct_customer_features order by event_ts"
        ).fetch_df()
    finally:
        con.close()


def main() -> None:
    df = load_features()
    X = df[FEATURES].astype(float)
    y = df[LABEL_COLUMN].astype(int)

    split = int(len(df) * 0.8)  # time-ordered split
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]

    model = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.1, random_state=42
    )
    model.fit(X_train, y_train)
    scores = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, scores)
    ap = average_precision_score(y_test, scores)
    print(f"test AUC={auc:.4f}  average_precision={ap:.4f}  base_rate={y_test.mean():.2%}")

    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("fraud-detection")
    with mlflow.start_run(run_name="train-fraud-model") as run:
        mlflow.log_params(
            {
                "algorithm": "GradientBoostingClassifier",
                "n_estimators": 150,
                "max_depth": 3,
                "learning_rate": 0.1,
                "features": ",".join(FEATURES),
                "label_column": LABEL_COLUMN,
                "event_ts_column": EVENT_TS_COLUMN,
                "training_table": "warehouse.main.fct_customer_features",
            }
        )
        mlflow.log_metrics({"test_auc": auc, "test_average_precision": ap})
        signature = infer_signature(X_train, model.predict_proba(X_train)[:, 1])
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            signature=signature,
            registered_model_name=MODEL_NAME,
        )
        run_id = run.info.run_id

    client = mlflow.MlflowClient()
    for key, value in {
        "label_column": LABEL_COLUMN,
        "event_ts_column": EVENT_TS_COLUMN,
        "features": ",".join(FEATURES),
        "training_table": "warehouse.main.fct_customer_features",
    }.items():
        client.set_registered_model_tag(MODEL_NAME, key, value)

    latest = max(
        int(v.version) for v in client.search_model_versions(f"name='{MODEL_NAME}'")
    )
    client.set_registered_model_alias(MODEL_NAME, "champion", str(latest))
    print(f"registered {MODEL_NAME} v{latest} (run {run_id}) at {TRACKING_URI}")


if __name__ == "__main__":
    main()
