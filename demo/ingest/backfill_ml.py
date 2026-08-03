"""Stitch the full ML lineage chain in DataHub, regardless of connector gaps.

Guarantees, idempotently:
  fct_customer_features (duckdb dataset)
    -> mlFeature per model feature (sources = the feature table dataset)
    -> mlFeatureTable "customer_features" grouping them
    -> mlModel fraud_model (consumes the features; carries label/event-ts
       custom properties for the leakage auditor)
    -> mlModelDeployment "fraud-scoring-prod" (env=PROD, IN_SERVICE)

The MLflow connector's model URN is discovered via search so we enrich the
same entity it created instead of forking a parallel one. Read-modify-write
on existing aspects so connector-emitted metadata is preserved.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from datahub.emitter.mce_builder import (
    make_dataset_urn,
    make_ml_feature_table_urn,
    make_ml_feature_urn,
    make_ml_model_deployment_urn,
    make_ml_model_urn,
)
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    DeploymentStatusClass,
    MLFeaturePropertiesClass,
    MLFeatureTablePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
)

FEATURE_TABLE_NAME = "customer_features"
FEATURES = {
    "txn_count_30d": "Transactions by this customer in the trailing 30 days",
    "avg_amount_30d": "Average amount (USD) for this customer over the trailing 30 days",
    "distinct_merchants_30d": "Distinct merchants used in the trailing 30 days",
    "country_risk": "Static risk score of the transaction country",
    "chargebacks_next_30d": "Customer chargebacks in the 30 days AFTER event_ts",
}
MODEL_PROPS = {
    "label_column": "is_fraud",
    "event_ts_column": "event_ts",
    "training_table": "warehouse.main.fct_customer_features",
}

FCT_URN = make_dataset_urn("duckdb", "warehouse.main.fct_customer_features", "PROD")
RAW_URN = make_dataset_urn("duckdb", "warehouse.main.raw_transactions", "PROD")


def connect() -> DataHubGraph:
    return DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )


def find_model_urn(graph: DataHubGraph) -> str:
    """Prefer the mlModel the MLflow connector created; fall back to our own URN."""
    results = list(
        graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=20)
    )
    if results:
        # Highest version sorts last with the connector's name_version convention.
        return sorted(results)[-1]
    return make_ml_model_urn("mlflow", "fraud_model", "PROD")


load_dotenv()

def main() -> int:
    graph = connect()

    feature_urns = []
    for name, description in FEATURES.items():
        furn = make_ml_feature_urn(FEATURE_TABLE_NAME, name)
        feature_urns.append(furn)
        existing = graph.get_aspect(furn, MLFeaturePropertiesClass)
        props = existing or MLFeaturePropertiesClass()
        props.description = description
        sources = set(props.sources or [])
        sources.add(FCT_URN)
        props.sources = sorted(sources)
        graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=furn, aspect=props))

    ft_urn = make_ml_feature_table_urn("duckdb", FEATURE_TABLE_NAME)
    ft_props = graph.get_aspect(ft_urn, MLFeatureTablePropertiesClass) or MLFeatureTablePropertiesClass()
    ft_props.description = "Features consumed by fraud_model, built by dbt from the DuckDB warehouse."
    ft_props.mlFeatures = feature_urns
    graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=ft_urn, aspect=ft_props))

    model_urn = find_model_urn(graph)
    dep_urn = make_ml_model_deployment_urn("mlflow", "fraud-scoring-prod", "PROD")

    dep_props = MLModelDeploymentPropertiesClass(
        description="Production fraud-scoring endpoint serving fraud_model.",
        customProperties={"env": "PROD", "endpoint": "https://scoring.internal/fraud"},
        status=DeploymentStatusClass.IN_SERVICE,
    )
    graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=dep_urn, aspect=dep_props))

    model_props = graph.get_aspect(model_urn, MLModelPropertiesClass) or MLModelPropertiesClass()
    model_props.mlFeatures = feature_urns
    deployments = set(model_props.deployments or [])
    deployments.add(dep_urn)
    model_props.deployments = sorted(deployments)
    custom = dict(model_props.customProperties or {})
    custom.update(MODEL_PROPS)
    model_props.customProperties = custom
    if not model_props.description:
        model_props.description = "Gradient-boosted fraud classifier trained on customer_features."
    graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=model_urn, aspect=model_props))

    print("backfilled ML chain:")
    print(f"  feature table : {ft_urn}")
    print(f"  features      : {len(feature_urns)} (sources -> {FCT_URN})")
    print(f"  model         : {model_urn}")
    print(f"  deployment    : {dep_urn}")
    print(f"  watch this    : {RAW_URN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
