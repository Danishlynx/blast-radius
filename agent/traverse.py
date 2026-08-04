"""Traverse: walk downstream lineage from a ChangeEvent to the blast radius.

Strategy (validated against DataHub v1.6.0 with dbt-sourced lineage):
- Node discovery is TABLE-level via the MCP `get_lineage` tool (multi-hop,
  returns datasets, mlFeatures, and mlModels with hop degrees). This is
  complete and robust.
- Column tracking joins the fine-grained lineage aspects of every discovered
  dataset (SDK read) and propagates the changed columns through them.
  dbt/duckdb sibling datasets are normalized by table name so the sibling
  hop doesn't break column flow.
- mlModel deployments are not lineage edges; they come from the model's
  properties (MCP `get_entities`).

If no dataset on the path carries fine-grained lineage for the changed
column, the result degrades to table-level confidence — every downstream
column is treated as potentially affected, and the evidence says so.
"""

from __future__ import annotations

import re
from collections import defaultdict

from datahub.ingestion.graph.client import DataHubGraph

from agent.adapters import sdk_read
from agent.adapters.mcp_read import McpDataHub
from agent.models import BlastNode, BlastRadius, ChangeEvent

MAX_HOPS = 6

_SCHEMA_FIELD_RE = re.compile(
    r"urn:li:schemaField:\((?P<dataset>urn:li:dataset:\(.*?\)),(?P<column>[^)]+)\)"
)
_DATASET_NAME_RE = re.compile(r"urn:li:dataset:\(urn:li:dataPlatform:[^,]+,(?P<name>[^,]+),")


def table_key(dataset_urn: str) -> str:
    """Platform-agnostic key so dbt/duckdb siblings share column state."""
    m = _DATASET_NAME_RE.search(dataset_urn)
    return m.group("name") if m else dataset_urn


def parse_schema_field(urn: str) -> tuple[str, str] | None:
    m = _SCHEMA_FIELD_RE.match(urn)
    return (m.group("dataset"), m.group("column")) if m else None


async def _discover_nodes(mcp: McpDataHub, root: str) -> list[BlastNode]:
    out = await mcp.call(
        "get_lineage", urn=root, upstream=False, max_hops=MAX_HOPS, max_results=200
    )
    results = (out.get("downstreams") or {}).get("searchResults", [])
    nodes = []
    for r in results:
        e = r["entity"]
        nodes.append(
            BlastNode(
                urn=e["urn"],
                entity_type=_entity_type(e["urn"]),
                name=e.get("name", ""),
                platform=(e.get("platform") or {}).get("name", ""),
                hop=r.get("degree", 0),
            )
        )
    return sorted(nodes, key=lambda n: n.hop)


def _entity_type(urn: str) -> str:
    kind = urn.split(":")[2]
    return {
        "dataset": "dataset",
        "mlFeature": "mlFeature",
        "mlFeatureTable": "mlFeatureTable",
        "mlModel": "mlModel",
        "mlModelDeployment": "mlModelDeployment",
        "mlModelGroup": "mlModelGroup",
    }.get(kind, kind)


def _propagate_columns(
    graph: DataHubGraph,
    change: ChangeEvent,
    dataset_urns: list[str],
) -> tuple[dict[str, set[str]], list[list[str]], bool]:
    """Push the changed columns through fine-grained lineage.

    Real-world CLL is partial (the dbt source, for one, drops occasional
    passthrough edges), so explicit fine-grained edges are augmented with
    inferred name-match passthrough edges along table-level lineage: if an
    upstream and downstream dataset both have a column `x` and are linked,
    `x` flows through. Explicit edges still carry derived-column flow
    (amount_usd -> avg_amount_30d).

    Returns (affected columns per table key, column-path chains, had_cll).
    """
    changed = {c.before or c.after for c in change.columns if (c.before or c.after)}
    affected: dict[str, set[str]] = defaultdict(set)
    affected[table_key(change.entity_urn)] = set(changed)
    parents: dict[tuple[str, str], tuple[str, str]] = {}

    all_urns = [change.entity_urn] + [u for u in dataset_urns if u != change.entity_urn]
    schema_cols: dict[str, set[str]] = defaultdict(set)
    table_edges: set[tuple[str, str]] = set()
    for urn in all_urns:
        key = table_key(urn)
        schema_cols[key].update(c for c, _ in sdk_read.schema_fields(graph, urn))
        for up_urn in sdk_read.table_upstreams(graph, urn):
            table_edges.add((table_key(up_urn), key))
    # A renamed/dropped column is gone from the root's CURRENT schema but is
    # exactly what must flow downstream — seed it back in.
    schema_cols[table_key(change.entity_urn)].update(changed)

    edges = []  # (up_key, up_col, down_key, down_col)
    had_cll = False
    for urn in dataset_urns:
        for upstream_fields, downstream_fields in sdk_read.column_lineage(graph, urn):
            down_key = table_key(urn)
            for uf in upstream_fields:
                parsed = parse_schema_field(uf)
                if not parsed:
                    continue
                up_key = table_key(parsed[0])
                for df in downstream_fields:
                    down_col = (parse_schema_field(df) or (None, df.split(",")[-1].rstrip(")")))[1]
                    edges.append((up_key, parsed[1], down_key, down_col))
                    had_cll = True

    for up_key, down_key in table_edges:
        for col in schema_cols[up_key] & schema_cols[down_key]:
            edges.append((up_key, col, down_key, col))

    # Fixpoint propagation (the graph is tiny; simplicity over cleverness).
    for _ in range(MAX_HOPS + 2):
        grew = False
        for up_key, up_col, down_key, down_col in edges:
            if up_col in affected.get(up_key, set()) and down_col not in affected[down_key]:
                affected[down_key].add(down_col)
                parents[(down_key, down_col)] = (up_key, up_col)
                grew = True
        if not grew:
            break

    paths = []
    root_key = table_key(change.entity_urn)
    for (key, col), _ in list(parents.items()):
        if any((key, col) == p for p in parents.values()):
            continue  # not a leaf of the column flow
        chain = [f"{key}.{col}"]
        cursor = (key, col)
        while cursor in parents:
            cursor = parents[cursor]
            chain.append(f"{cursor[0]}.{cursor[1]}")
        if chain[-1].startswith(root_key):
            paths.append(list(reversed(chain)))
    return dict(affected), paths, had_cll


def _attach_ml_details(graph: DataHubGraph, nodes: list[BlastNode]) -> list[BlastNode]:
    """Resolve model deployments (a property edge, not a lineage edge)."""
    from datahub.metadata.schema_classes import (
        MLModelDeploymentPropertiesClass,
        MLModelPropertiesClass,
    )

    for model in [n for n in nodes if n.entity_type == "mlModel"]:
        props = graph.get_aspect(model.urn, MLModelPropertiesClass)
        dep_urns = list(props.deployments or []) if props else []
        model.deployed = bool(dep_urns)
        for dep in dep_urns:
            dprops = graph.get_aspect(dep, MLModelDeploymentPropertiesClass)
            env = (dprops.customProperties or {}).get("env") if dprops else None
            model.env = env or model.env
            if all(n.urn != dep for n in nodes):
                nodes.append(
                    BlastNode(
                        urn=dep,
                        entity_type="mlModelDeployment",
                        name=dep.split(",")[-2] if "," in dep else dep,
                        platform="mlflow",
                        hop=model.hop + 1,
                        env=env,
                    )
                )
    return nodes


async def compute_blast_radius(change: ChangeEvent, graph: DataHubGraph) -> BlastRadius:
    async with McpDataHub() as mcp:
        nodes = await _discover_nodes(mcp, change.entity_urn)
    nodes = _attach_ml_details(graph, nodes)

    dataset_urns = [n.urn for n in nodes if n.entity_type == "dataset"]
    affected, paths, had_cll = _propagate_columns(graph, change, dataset_urns)

    # Column-level narrowing only when the column flow actually resolved:
    # keep the features fed by affected feature-table columns, drop the rest.
    resolved = had_cll and bool(paths)
    if resolved:
        affected_cols = {col for cols in affected.values() for col in cols}
        kept = []
        for n in nodes:
            feature_name = n.urn.split(",")[-1].rstrip(")")
            if n.entity_type == "mlFeature":
                n.name = n.name or feature_name
                if feature_name not in affected_cols:
                    continue
            kept.append(n)
        nodes = kept

    for n in nodes:
        n.owners = sdk_read.owners(graph, n.urn)

    return BlastRadius(
        change=change,
        nodes=nodes,
        paths=paths,
        confidence="column-level" if resolved else "table-level",
    )
