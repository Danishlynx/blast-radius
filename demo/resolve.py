"""Resolve the demo incident: mark Blast Radius incidents RESOLVED and clear
the model-at-risk tag (the gate-green demo beat).

Only touches incidents whose description carries a Blast Radius evidence
hash — never someone else's incidents.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from datahub.emitter.mce_builder import make_dataset_urn  # noqa: E402

from agent.act import MODEL_AT_RISK_TAG  # noqa: E402
from agent.adapters import graphql, sdk_read, sdk_write  # noqa: E402
from agent.memory import hash_in_description  # noqa: E402

TARGETS = [
    make_dataset_urn("duckdb", "warehouse.main.fct_customer_features", "PROD"),
    make_dataset_urn("duckdb", "warehouse.main.raw_transactions", "PROD"),
]


def main() -> int:
    graph = sdk_read.connect()
    seen: set[str] = set()
    resolved = 0
    for target in TARGETS:
        for incident in graphql.active_incidents(target):
            if incident["urn"] in seen:
                continue  # one incident can be attached to several datasets
            if hash_in_description(incident.get("description", "")):
                seen.add(incident["urn"])
                graphql.resolve_incident(
                    incident["urn"], "Fix merged; supply chain restored. — Blast Radius"
                )
                resolved += 1
                print(f"resolved: {incident['urn']} ({incident['title']})")

    models = list(
        graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=20)
    )
    for model in models:
        sdk_write.remove_tag(graph, model, MODEL_AT_RISK_TAG)
    if models:
        print(f"cleared model-at-risk tag from {len(models)} model(s)")

    if resolved == 0:
        print("no active Blast Radius incidents found — nothing to resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
