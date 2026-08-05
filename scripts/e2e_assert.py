"""Acceptance assertions against a live DataHub GMS.

Modes (argv[1], default "world"):
  world            assertion 1 — the full lineage chain exists
  incidents-active assertion 2 — exactly 1 ACTIVE Blast Radius incident on the
                   feature table with an evidence hash; model-at-risk tag on
                   the model; run log present (run after break-it + scan)
  incidents-clear  post-resolve — 0 ACTIVE Blast Radius incidents; tag cleared

Exit code 0 = pass.
"""

from __future__ import annotations

import os
import sys

from datahub.emitter.mce_builder import (
    make_dataset_urn,
    make_ml_feature_urn,
    make_ml_model_deployment_urn,
)
from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.metadata.schema_classes import (
    MLFeaturePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    SchemaMetadataClass,
    UpstreamLineageClass,
)
from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't print the check marks.
for _stream in (sys.stdout, sys.stderr):
    if _stream.encoding and _stream.encoding.lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8")

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


load_dotenv()


def _connect() -> DataHubGraph:
    return DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )


def _our_incidents(urn: str) -> list[dict]:
    from agent.adapters import graphql
    from agent.memory import hash_in_description

    return [
        i for i in graphql.active_incidents(urn)
        if hash_in_description(i.get("description", ""))
    ]


def _model_tags(graph: DataHubGraph) -> list[str]:
    from datahub.metadata.schema_classes import GlobalTagsClass

    models = list(
        graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=10)
    )
    if not models:
        return []
    tags = graph.get_aspect(sorted(models)[-1], GlobalTagsClass)
    return [t.tag for t in (tags.tags if tags else [])]


def assert_incidents_active() -> int:
    from datahub.metadata.schema_classes import StructuredPropertiesClass

    graph = _connect()
    print("assertion 2/3: incident filed once, model tagged, run log written")

    ours = _our_incidents(FCT_DUCKDB)
    check("exactly 1 ACTIVE Blast Radius incident on the feature table", len(ours) == 1,
          f"found {len(ours)}")
    check("model-at-risk tag present on fraud_model",
          "urn:li:tag:model-at-risk" in _model_tags(graph))
    sp = graph.get_aspect(FCT_DUCKDB, StructuredPropertiesClass)
    props = {p.propertyUrn for p in (sp.properties if sp else [])}
    check("run log structured property present on the feature table",
          "urn:li:structuredProperty:io.blastradius.runLog" in props)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def assert_incidents_clear() -> int:
    import time

    graph = _connect()
    print("post-resolve: incidents resolved, tag cleared")

    # The incidents query is index-backed; give the resolve time to settle.
    deadline = time.monotonic() + 45
    ours = _our_incidents(FCT_DUCKDB)
    while ours and time.monotonic() < deadline:
        time.sleep(5)
        ours = _our_incidents(FCT_DUCKDB)
    check("0 ACTIVE Blast Radius incidents on the feature table", len(ours) == 0,
          f"found {len(ours)}")
    check("model-at-risk tag cleared from fraud_model",
          "urn:li:tag:model-at-risk" not in _model_tags(graph))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def assert_leakage() -> int:
    from datahub.metadata.schema_classes import GlobalTagsClass, MLModelPropertiesClass

    graph = _connect()
    print("assertion 4: structural leakage audit")

    leak_feature = "urn:li:mlFeature:(customer_features,chargebacks_next_30d)"
    clean_features = [
        f"urn:li:mlFeature:(customer_features,{n})"
        for n in ("txn_count_30d", "avg_amount_30d", "distinct_merchants_30d", "country_risk")
    ]
    tag = "urn:li:tag:leakage-suspect"

    def has_tag(urn: str) -> bool:
        gt = graph.get_aspect(urn, GlobalTagsClass)
        return any(t.tag == tag for t in (gt.tags if gt else []))

    check("chargebacks_next_30d flagged leakage-suspect", has_tag(leak_feature))
    check("the four honest features stay clean", not any(has_tag(u) for u in clean_features))

    models = list(
        graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=10)
    )
    model_ok = False
    report_ok = False
    if models:
        from datahub.metadata.schema_classes import EditableMLModelPropertiesClass

        model = sorted(models)[-1]
        model_ok = has_tag(model)
        # the MCP append may land on either the ingestion description or the
        # editable one (both render in the UI) — accept either
        props = graph.get_aspect(model, MLModelPropertiesClass)
        editable = graph.get_aspect(model, EditableMLModelPropertiesClass)
        combined = ((props.description if props else "") or "") + (
            (editable.description if editable else "") or ""
        )
        report_ok = "Leakage audit" in combined
    check("model tagged leakage-suspect", model_ok)
    check("audit report appended to model docs", report_ok)

    leak_incidents = [
        i for i in _our_incidents(FCT_DUCKDB) if i.get("title", "").startswith("[LEAKAGE]")
    ]
    check("exactly 1 ACTIVE leakage incident on the feature table", len(leak_incidents) == 1,
          f"found {len(leak_incidents)}")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def main() -> int:
    graph = _connect()

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
    mode = sys.argv[1] if len(sys.argv) > 1 else "world"
    if mode == "incidents-active":
        sys.exit(assert_incidents_active())
    if mode == "incidents-clear":
        sys.exit(assert_incidents_clear())
    if mode == "leakage":
        sys.exit(assert_leakage())
    sys.exit(main())
