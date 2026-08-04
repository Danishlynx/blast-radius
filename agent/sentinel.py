"""Detect: turn schema changes into normalized ChangeEvents.

Day 2 ships the CLI-trigger path: diff the two most recent schemaMetadata
versions in DataHub and synthesize a ChangeEvent. The poller daemon and the
Kafka consumer (Day 3) reuse `diff_schemas` so all triggers emit identical
events.
"""

from __future__ import annotations

from datahub.ingestion.graph.client import DataHubGraph

from agent.adapters import sdk_read
from agent.models import ChangeEvent, ColumnChange


def diff_schemas(
    before: list[tuple[str, str]], after: list[tuple[str, str]]
) -> tuple[str, list[ColumnChange]]:
    """Classify the difference between two schema versions.

    Returns (change_type, column changes). A single removed+added pair is
    treated as a rename (the classic silent migration); type changes on
    surviving columns are captured alongside.
    """
    before_map = dict(before)
    after_map = dict(after)
    removed = [c for c in before_map if c not in after_map]
    added = [c for c in after_map if c not in before_map]
    retyped = [
        c for c in before_map
        if c in after_map and before_map[c] != after_map[c]
    ]

    columns: list[ColumnChange] = []
    if len(removed) == 1 and len(added) == 1:
        change_type = "rename"
        columns.append(
            ColumnChange(
                before=removed[0],
                after=added[0],
                type_before=before_map[removed[0]],
                type_after=after_map[added[0]],
            )
        )
    elif removed:
        change_type = "drop"
        columns += [ColumnChange(before=c, type_before=before_map[c]) for c in removed]
        columns += [ColumnChange(after=c, type_after=after_map[c]) for c in added]
    elif retyped:
        change_type = "type_change"
    elif added:
        change_type = "add"
        columns += [ColumnChange(after=c, type_after=after_map[c]) for c in added]
    else:
        change_type = "other"

    columns += [
        ColumnChange(before=c, after=c, type_before=before_map[c], type_after=after_map[c])
        for c in retyped
    ]
    return change_type, columns


def synthesize_change_event(graph: DataHubGraph, urn: str) -> ChangeEvent | None:
    """CLI trigger: diff the latest two schema versions of `urn` in DataHub.

    Returns None when there is no prior version or no difference.
    """
    history = sdk_read.schema_history(graph, urn)
    if len(history) < 2:
        return None
    before, after = history[-2], history[-1]
    change_type, columns = diff_schemas(before, after)
    if not columns and change_type == "other":
        return None
    return ChangeEvent(
        source="cli",
        entity_urn=urn,
        change_type=change_type,
        columns=columns,
        raw={"versions_compared": [len(history) - 1, len(history)]},
    )
