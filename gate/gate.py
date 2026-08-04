"""Deployment circuit breaker: ask DataHub before letting a model ship.

Resolves the model's upstream lineage, collects ACTIVE incidents on those
assets and risk tags on the model itself, and fails the CI job while any
exist. This extends DataHub's documented circuit-breaker pattern (block
pipelines whose inputs have active incidents) to ML deploy workflows.

Standalone by design: env vars in, exit code out, GitHub job summary when
running in Actions. Only dependency is `requests`.

Env: DATAHUB_GMS_URL, DATAHUB_TOKEN, MODEL_URN (optional — discovers the
newest mlModel matching MODEL_QUERY, default "fraud_model", when unset).
"""

from __future__ import annotations

import os
import sys

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")
BLOCKING_TAGS = ("urn:li:tag:model-at-risk", "urn:li:tag:leakage-suspect")
FRONTEND = os.environ.get("DATAHUB_FRONTEND_URL", "http://localhost:9002")
MAX_HOPS_RESULTS = 200


def gql(query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        f"{GMS}/api/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {os.environ.get('DATAHUB_TOKEN', '')}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def discover_model() -> str | None:
    data = gql(
        """
        query find($q: String!) {
          searchAcrossEntities(input: {query: $q, types: [MLMODEL], start: 0, count: 10}) {
            searchResults { entity { urn } }
          }
        }
        """,
        {"q": os.environ.get("MODEL_QUERY", "fraud_model")},
    )
    urns = sorted(r["entity"]["urn"] for r in data["searchAcrossEntities"]["searchResults"])
    return urns[-1] if urns else None


def upstream_datasets(model_urn: str) -> list[str]:
    data = gql(
        """
        query up($input: SearchAcrossLineageInput!) {
          searchAcrossLineage(input: $input) {
            searchResults { entity { urn type } }
          }
        }
        """,
        {"input": {"urn": model_urn, "direction": "UPSTREAM", "query": "*",
                   "start": 0, "count": MAX_HOPS_RESULTS}},
    )
    return [
        r["entity"]["urn"]
        for r in data["searchAcrossLineage"]["searchResults"]
        if r["entity"]["type"] == "DATASET"
    ]


def active_incidents(dataset_urn: str) -> list[dict]:
    data = gql(
        """
        query inc($urn: String!) {
          dataset(urn: $urn) {
            incidents(state: ACTIVE, start: 0, count: 50) {
              incidents { urn title }
            }
          }
        }
        """,
        {"urn": dataset_urn},
    )
    result = (data.get("dataset") or {}).get("incidents") or {}
    return [{**i, "on": dataset_urn} for i in (result.get("incidents") or [])]


def model_risk_tags(model_urn: str) -> list[str]:
    data = gql(
        """
        query tags($urn: String!) {
          entity(urn: $urn) {
            ... on MLModel { tags { tags { tag { urn } } } }
          }
        }
        """,
        {"urn": model_urn},
    )
    tags = ((data.get("entity") or {}).get("tags") or {}).get("tags") or []
    present = {t["tag"]["urn"] for t in tags}
    return [t for t in BLOCKING_TAGS if t in present]


def entity_link(urn: str) -> str:
    from urllib.parse import quote

    kind = urn.split(":")[2]
    path = {"dataset": "dataset", "mlModel": "mlModels"}.get(kind, kind)
    return f"{FRONTEND}/{path}/{quote(urn, safe='')}"


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main() -> int:
    model_urn = os.environ.get("MODEL_URN") or discover_model()
    if not model_urn:
        print("gate: no model found — refusing to pass an unverifiable deploy")
        return 1
    print(f"gate: checking supply chain of {model_urn}")

    upstreams = upstream_datasets(model_urn)
    print(f"gate: {len(upstreams)} upstream dataset(s) in lineage")

    blockers: list[str] = []
    seen: set[str] = set()
    for ds in upstreams:
        for inc in active_incidents(ds):
            if inc["urn"] in seen:
                continue
            seen.add(inc["urn"])
            blockers.append(
                f"ACTIVE incident **{inc['title']}** on `{inc['on'].split(',')[1]}` "
                f"([view]({entity_link(inc['on'])}))"
            )
    for tag in model_risk_tags(model_urn):
        blockers.append(
            f"tag `{tag.split(':')[-1]}` on the model ([view]({entity_link(model_urn)}))"
        )

    if blockers:
        lines = ["## 🛑 Deployment blocked by Blast Radius", "",
                 "DataHub reports the model's supply chain is not healthy:", ""]
        lines += [f"- {b}" for b in blockers]
        lines += ["", "Resolve the incident(s) (`make resolve` in the demo) and re-run."]
        write_summary(lines)
        print("\n".join(lines))
        return 1

    write_summary(["## ✅ Supply chain healthy",
                   f"No active incidents or risk tags upstream of `{model_urn}`."])
    print("gate: supply chain healthy — deploy may proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
