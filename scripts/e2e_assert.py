"""Acceptance assertions against a live DataHub GMS.

Assertion 1 (Day 1): the full chain
  raw_transactions -> fct_customer_features -> mlFeature(s) -> fraud_model
  -> fraud-scoring-prod
exists with column-level lineage on the amount path.

Later assertions (2-5) are added as their features land. Exit code 0 = pass.
"""

from __future__ import annotations

import os
import sys

from datahub.emitter.mce_builder import (
    make_dataset_urn,
    make_ml_feature_urn,
    make_ml_model_deployment_urn,
)
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
    UpstreamLineageClass,
)

RAW_DUCKDB = make_dataset_urn("duckdb", "warehouse.main.raw_transactions", "PROD")
FCT_DUCKDB = make_dataset_urn("duckdb", "warehouse.main.fct_customer_features", "PROD")
FCT_DBT = make_dataset_urn("dbt", "warehouse.main.fct_customer_features", "PROD")
STG_DBT = make_dataset_urn("dbt", "warehouse.main.stg_transactions", "PROD")
DEPLOYMENT = make_ml_model_deployment_urn("mlflow", "fraud-scoring-prod", "PROD")
FEATURE = make_ml_feature_urn("customer_features", "avg_amount_30d")

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}" + (f" — {detail}" if detail else ""))


def downstream_urns(graph: DataHubGraph, urn: str) -> set[str]:
    query = """
    query lineage($input: SearchAcrossLineageInput!) {
      searchAcrossLineage(input: $input) {
        searchResults { entity { urn } }
      }
    }
    """
    variables = {
        "input": {
            "urn": urn,
            "direction": "DOWNSTREAM",
            "query": "*",
            "start": 0,
            "count": 100,
        }
    }
    result = graph.execute_graphql(query, variables=variables)
    return {
        r["entity"]["urn"]
        for r in result["searchAcrossLineage"]["searchResults"]
    }


def main() -> int:
    graph = DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )

    print("assertion 1: the world exists, end to end")

    schema = graph.get_aspect(RAW_DUCKDB, SchemaMetadataClass)
    fields = [f.fieldPath for f in schema.fields] if schema else []
    check(
        "raw_transactions (duckdb) has a schema in DataHub",
        bool(fields),
        f"urn={RAW_DUCKDB}",
    )
    check(
        "raw_transactions schema contains amount column (amount_usd pre-break / amount post-break)",
        any(f in ("amount_usd", "amount") for f in fields),
        f"fields={fields}",
    )

    downstream = downstream_urns(graph, RAW_DUCKDB)
    check(
        "fct_customer_features is downstream of raw_transactions",
        FCT_DUCKDB in downstream or FCT_DBT in downstream,
        f"downstream={sorted(downstream)[:10]}",
    )

    cll_found = False
    for urn in (FCT_DBT, FCT_DUCKDB, STG_DBT):
        ul = graph.get_aspect(urn, UpstreamLineageClass)
        for fgl in (ul.fineGrainedLineages if ul and ul.fineGrainedLineages else []):
            ups = " ".join(fgl.upstreams or [])
            if "amount" in ups:
                cll_found = True
                break
        if cll_found:
            break
    check("column-level lineage captured for the amount path", cll_found)

    fprops = graph.get_aspect(FEATURE, MLFeaturePropertiesClass)
    check(
        "mlFeature avg_amount_30d exists with the feature table as source",
        bool(fprops) and FCT_DUCKDB in (fprops.sources or []),
    )

    models = list(
        graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=10)
    )
    check("mlModel fraud_model exists", bool(models))
    model_ok = False
    if models:
        mprops = graph.get_aspect(sorted(models)[-1], MLModelPropertiesClass)
        model_ok = (
            bool(mprops)
            and FEATURE in (mprops.mlFeatures or [])
            and DEPLOYMENT in (mprops.deployments or [])
        )
    check("fraud_model consumes the mlFeatures and links the deployment", model_ok)

    dprops = graph.get_aspect(DEPLOYMENT, MLModelDeploymentPropertiesClass)
    check(
        "mlModelDeployment fraud-scoring-prod exists with env=PROD",
        bool(dprops) and (dprops.customProperties or {}).get("env") == "PROD",
    )

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
