"""Thin DataHub SDK read adapter. Zero business logic.

Covers reads the MCP server doesn't expose at aspect granularity:
schemaMetadata version history, fine-grained (column) lineage aspects,
and ownership.
"""

from __future__ import annotations

import os

from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.metadata.schema_classes import (
    OwnershipClass,
    SchemaMetadataClass,
    UpstreamLineageClass,
)


def connect() -> DataHubGraph:
    return DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )


def schema_fields(graph: DataHubGraph, urn: str, version: int = 0) -> list[tuple[str, str]]:
    """[(fieldPath, nativeDataType)] for one schemaMetadata version; [] if absent."""
    sm = graph.get_aspect(urn, SchemaMetadataClass, version=version)
    return [(f.fieldPath, f.nativeDataType or "") for f in sm.fields] if sm else []


def schema_history(graph: DataHubGraph, urn: str, max_versions: int = 25) -> list[list[tuple[str, str]]]:
    """All schema versions oldest -> newest.

    GMS archives prior versions at 1..K (oldest first) and serves the current
    one at version 0 — so the full history is [v1..vK, v0].
    """
    versions = []
    for v in range(1, max_versions + 1):
        fields = schema_fields(graph, urn, version=v)
        if not fields:
            break
        versions.append(fields)
    current = schema_fields(graph, urn)
    if current:
        versions.append(current)
    return versions


def table_upstreams(graph: DataHubGraph, urn: str) -> list[str]:
    """Table-level upstream dataset URNs of one dataset."""
    ul = graph.get_aspect(urn, UpstreamLineageClass)
    return [u.dataset for u in ul.upstreams] if ul else []


def column_lineage(graph: DataHubGraph, urn: str) -> list[tuple[list[str], list[str]]]:
    """Fine-grained lineage of one dataset: [(upstream schemaField urns, downstream field urns)]."""
    ul = graph.get_aspect(urn, UpstreamLineageClass)
    if not ul or not ul.fineGrainedLineages:
        return []
    return [(list(f.upstreams or []), list(f.downstreams or [])) for f in ul.fineGrainedLineages]


def owners(graph: DataHubGraph, urn: str) -> list[str]:
    ow = graph.get_aspect(urn, OwnershipClass)
    return [o.owner for o in ow.owners] if ow else []
