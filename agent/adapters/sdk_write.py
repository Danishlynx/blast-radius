"""Thin SDK write adapter. Zero business logic.

Covers writes the MCP server can't do (tag-entity creation, structured
properties on OSS) and serves as the fallback path when an MCP mutation
fails.
"""

from __future__ import annotations

from datahub.emitter.mce_builder import make_data_platform_urn  # noqa: F401  (re-export site)
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DataHubGraph
from datahub.metadata.schema_classes import (
    GlobalTagsClass,
    MLModelPropertiesClass,
    PropertyValueClass,
    StructuredPropertiesClass,
    StructuredPropertyDefinitionClass,
    StructuredPropertyValueAssignmentClass,
    TagAssociationClass,
    TagPropertiesClass,
)


def ensure_tag(graph: DataHubGraph, tag_urn: str, name: str, description: str, color: str) -> None:
    if graph.exists(tag_urn):
        return
    graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=tag_urn,
            aspect=TagPropertiesClass(name=name, description=description, colorHex=color),
        )
    )


def add_tag(graph: DataHubGraph, entity_urn: str, tag_urn: str) -> None:
    tags = graph.get_aspect(entity_urn, GlobalTagsClass) or GlobalTagsClass(tags=[])
    if not any(t.tag == tag_urn for t in tags.tags):
        tags.tags.append(TagAssociationClass(tag=tag_urn))
        graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=entity_urn, aspect=tags))


def remove_tag(graph: DataHubGraph, entity_urn: str, tag_urn: str) -> None:
    tags = graph.get_aspect(entity_urn, GlobalTagsClass)
    if tags and any(t.tag == tag_urn for t in tags.tags):
        tags.tags = [t for t in tags.tags if t.tag != tag_urn]
        graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=entity_urn, aspect=tags))


def append_model_description(graph: DataHubGraph, model_urn: str, text: str) -> None:
    props = graph.get_aspect(model_urn, MLModelPropertiesClass) or MLModelPropertiesClass()
    props.description = (props.description or "") + text
    graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=model_urn, aspect=props))


RUN_LOG_PROPERTY = "urn:li:structuredProperty:io.blastradius.runLog"


def ensure_run_log_property(graph: DataHubGraph) -> None:
    if graph.exists(RUN_LOG_PROPERTY):
        return
    graph.emit_mcp(
        MetadataChangeProposalWrapper(
            entityUrn=RUN_LOG_PROPERTY,
            aspect=StructuredPropertyDefinitionClass(
                qualifiedName="io.blastradius.runLog",
                displayName="Blast Radius run log",
                valueType="urn:li:dataType:datahub.string",
                entityTypes=["urn:li:entityType:datahub.dataset"],
                description="Latest Blast Radius agent run against this asset (JSON).",
            ),
        )
    )


def set_run_log(graph: DataHubGraph, dataset_urn: str, payload_json: str) -> None:
    ensure_run_log_property(graph)
    existing = graph.get_aspect(dataset_urn, StructuredPropertiesClass) or StructuredPropertiesClass(
        properties=[]
    )
    existing.properties = [
        p for p in existing.properties if p.propertyUrn != RUN_LOG_PROPERTY
    ]
    existing.properties.append(
        StructuredPropertyValueAssignmentClass(
            propertyUrn=RUN_LOG_PROPERTY,
            values=[payload_json],
        )
    )
    graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=dataset_urn, aspect=existing))


__all__ = [
    "PropertyValueClass",
    "ensure_tag",
    "add_tag",
    "remove_tag",
    "append_model_description",
    "ensure_run_log_property",
    "set_run_log",
    "RUN_LOG_PROPERTY",
]
