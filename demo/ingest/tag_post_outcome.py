"""Apply the `post-outcome` tag to the chargebacks tables.

The tag marks assets whose rows describe events that happen AFTER a
transaction's event_ts. The leakage auditor's temporal rule (L2) flags any
feature whose lineage crosses a post-outcome asset.
"""

from __future__ import annotations

import os
import sys
import time

from datahub.emitter.mce_builder import make_dataset_urn, make_tag_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    GlobalTagsClass,
    TagAssociationClass,
    TagPropertiesClass,
)

TAG_URN = make_tag_urn("post-outcome")

TARGETS = [
    make_dataset_urn("duckdb", "warehouse.main.chargebacks", "PROD"),
    make_dataset_urn("dbt", "warehouse.main.chargebacks", "PROD"),
    make_dataset_urn("duckdb", "warehouse.main.stg_chargebacks", "PROD"),
    make_dataset_urn("dbt", "warehouse.main.stg_chargebacks", "PROD"),
]


def main() -> int:
    graph = DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )

    graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=TAG_URN,
            aspect=TagPropertiesClass(
                name="post-outcome",
                description=(
                    "Rows in this asset describe events that occur AFTER a "
                    "transaction's event_ts. Features derived from it can leak "
                    "the outcome into training data."
                ),
                colorHex="#d0021b",
            ),
        )
    )

    tagged = 0
    for urn in TARGETS:
        if not graph.exists(urn):
            print(f"  skip (not found): {urn}")
            continue
        tags = graph.get_aspect(urn, GlobalTagsClass) or GlobalTagsClass(tags=[])
        if not any(t.tag == TAG_URN for t in tags.tags):
            tags.tags.append(TagAssociationClass(tag=TAG_URN))
            graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=tags))
        tagged += 1
        print(f"  tagged post-outcome: {urn}")

    if tagged == 0:
        print("no chargebacks datasets found in DataHub — run 'make ingest' first", file=sys.stderr)
        return 1
    # Give GMS a moment to index before anything queries by tag.
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
