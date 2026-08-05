"""Structural target-leakage auditor.

Walks each feature's lineage BACKWARDS asking "does any feature contain the
answer?" — from lineage and captured SQL alone, no access to raw data.

Rules (evaluated per mlFeature the model consumes):
  L1 (direct):   any upstream column path includes the label column  -> LEAK
  L2 (temporal): any path crosses an asset tagged `post-outcome`     -> LEAK
  L3 (window):   the feature's defining SQL contains forward-looking
                 time arithmetic past the event timestamp            -> SUSPECT
                 (regex prefilter; LLM confirmation when configured)

Structural detection is high-precision on lineage evidence, not a statistical
guarantee — every report says which rule fired and why.

The evaluation core is pure and unit-tested; DataHub I/O lives at the edges.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from datahub.ingestion.graph.client import DataHubGraph
from pydantic import BaseModel

from agent.adapters import sdk_read
from agent.traverse import parse_schema_field, table_key

POST_OUTCOME_TAG = "urn:li:tag:post-outcome"
LEAKAGE_TAG = "urn:li:tag:leakage-suspect"
MAX_HOPS = 6

# Forward-looking time arithmetic: "<event_ts> + interval", "dateadd(..., <event_ts>)",
# or lead() windows. Trailing windows ("<event_ts> - interval") deliberately don't match.
_FORWARD_SQL = [
    r"\+\s*interval",
    r"dateadd\s*\(",
    r"\blead\s*\(",
    r"following\b",
]


class FeatureVerdict(BaseModel):
    feature: str
    verdict: str  # "LEAK" | "SUSPECT" | "CLEAN"
    rule: str | None = None  # "L1" | "L2" | "L3"
    reason: str = ""
    path: list[str] = []
    sql_snippet: str = ""


class AuditReport(BaseModel):
    model_urn: str
    label_column: str
    event_ts_column: str
    verdicts: list[FeatureVerdict] = []
    audit_hash: str = ""

    @property
    def leaks(self) -> list[FeatureVerdict]:
        return [v for v in self.verdicts if v.verdict == "LEAK"]

    @property
    def suspects(self) -> list[FeatureVerdict]:
        return [v for v in self.verdicts if v.verdict == "SUSPECT"]


# ---------------------------------------------------------------------------
# Pure core (unit-tested)


def sql_snippet_for(sql: str, feature: str) -> str:
    """The lines defining one feature in captured SQL.

    A small window around EACH occurrence (merged when overlapping) — never a
    span from first to last mention, which would swallow unrelated CTEs
    sitting between a feature's definition and the final select list.
    """
    lines = sql.splitlines()
    hits = [i for i, ln in enumerate(lines) if re.search(rf"\b{re.escape(feature)}\b", ln)]
    if not hits:
        return ""
    windows: list[tuple[int, int]] = []
    for h in hits:
        lo, hi = max(0, h - 4), min(len(lines), h + 3)
        if windows and lo <= windows[-1][1]:
            windows[-1] = (windows[-1][0], hi)
        else:
            windows.append((lo, hi))
    return "\n...\n".join("\n".join(lines[lo:hi]).strip() for lo, hi in windows)


def forward_looking(snippet: str, event_ts_column: str) -> bool:
    s = snippet.lower()
    if event_ts_column.lower() not in s:
        return False
    return any(re.search(p, s) for p in _FORWARD_SQL)


def evaluate_feature(
    feature: str,
    upstream_columns: set[tuple[str, str]],  # (table key, column)
    tags_by_table: dict[str, set[str]],  # table key -> tag urns
    label_column: str,
    event_ts_column: str,
    sql_snippet: str,
    path_to: dict[tuple[str, str], list[str]] | None = None,
) -> FeatureVerdict:
    paths = path_to or {}

    for key, col in sorted(upstream_columns):
        if col == label_column:
            return FeatureVerdict(
                feature=feature, verdict="LEAK", rule="L1",
                reason=f"derived from the label column `{label_column}` on `{key}`",
                path=paths.get((key, col), [f"{key}.{col}"]), sql_snippet=sql_snippet,
            )

    for key, col in sorted(upstream_columns):
        if POST_OUTCOME_TAG in tags_by_table.get(key, set()):
            return FeatureVerdict(
                feature=feature, verdict="LEAK", rule="L2",
                reason=f"lineage crosses `{key}` (tagged post-outcome) via `{col}` — "
                       "information from after the prediction timestamp",
                path=paths.get((key, col), [f"{key}.{col}"]), sql_snippet=sql_snippet,
            )

    if forward_looking(sql_snippet, event_ts_column):
        return FeatureVerdict(
            feature=feature, verdict="SUSPECT", rule="L3",
            reason=f"defining SQL extends past `{event_ts_column}` "
                   "(forward-looking time window)",
            sql_snippet=sql_snippet,
        )

    return FeatureVerdict(feature=feature, verdict="CLEAN", sql_snippet="")


# ---------------------------------------------------------------------------
# Graph I/O


def _collect_upstream_graph(graph: DataHubGraph, start_urns: list[str]) -> tuple[
    list[tuple[str, str, str, str]], dict[str, list[str]], set[str]
]:
    """(edges down<-up as (down_key, down_col, up_key, up_col)), platform urns
    per table key, and all dataset urns reached."""
    seen: set[str] = set()
    frontier = list(start_urns)
    urns_by_key: dict[str, list[str]] = defaultdict(list)
    edges: list[tuple[str, str, str, str]] = []

    for _ in range(MAX_HOPS):
        next_frontier = []
        for urn in frontier:
            if urn in seen:
                continue
            seen.add(urn)
            urns_by_key[table_key(urn)].append(urn)
            for upstream_fields, downstream_fields in sdk_read.column_lineage(graph, urn):
                for uf in upstream_fields:
                    parsed = parse_schema_field(uf)
                    if not parsed:
                        continue
                    for df in downstream_fields:
                        down_col = (parse_schema_field(df) or (None, df.split(",")[-1].rstrip(")")))[1]
                        edges.append((table_key(urn), down_col, table_key(parsed[0]), parsed[1]))
            next_frontier.extend(sdk_read.table_upstreams(graph, urn))
        frontier = next_frontier
        if not frontier:
            break

    # name-match passthrough for gaps in explicit CLL (same rationale as traverse)
    cols_by_key: dict[str, set[str]] = defaultdict(set)
    for key, urns in urns_by_key.items():
        for urn in urns:
            cols_by_key[key].update(c for c, _ in sdk_read.schema_fields(graph, urn))
    for key, urns in urns_by_key.items():
        for urn in urns:
            for up_urn in sdk_read.table_upstreams(graph, urn):
                up_key = table_key(up_urn)
                for col in cols_by_key[key] & cols_by_key.get(up_key, set()):
                    edges.append((key, col, up_key, col))

    return edges, dict(urns_by_key), seen


def upstream_closure(
    edges: list[tuple[str, str, str, str]], start: tuple[str, str]
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """All (table, column) reachable upstream of `start`, with a sample path each."""
    closure: set[tuple[str, str]] = set()
    paths: dict[tuple[str, str], list[str]] = {}
    frontier = [(start, [f"{start[0]}.{start[1]}"])]
    while frontier:
        (key, col), path = frontier.pop()
        for d_key, d_col, u_key, u_col in edges:
            if (d_key, d_col) == (key, col) and (u_key, u_col) not in closure:
                closure.add((u_key, u_col))
                new_path = path + [f"{u_key}.{u_col}"]
                paths[(u_key, u_col)] = new_path
                frontier.append(((u_key, u_col), new_path))
    return closure, paths


def audit(graph: DataHubGraph, model_urn: str) -> AuditReport:
    from datahub.metadata.schema_classes import GlobalTagsClass, MLModelPropertiesClass

    props = graph.get_aspect(model_urn, MLModelPropertiesClass)
    if not props:
        raise RuntimeError(f"model not found: {model_urn}")
    custom = props.customProperties or {}
    label_column = custom.get("label_column", "is_fraud")
    event_ts_column = custom.get("event_ts_column", "event_ts")
    feature_urns = list(props.mlFeatures or [])

    # feature -> source dataset(s)
    from datahub.metadata.schema_classes import MLFeaturePropertiesClass

    feature_sources: dict[str, list[str]] = {}
    for furn in feature_urns:
        name = furn.split(",")[-1].rstrip(")")
        fprops = graph.get_aspect(furn, MLFeaturePropertiesClass)
        feature_sources[name] = list(fprops.sources or []) if fprops else []

    all_sources = sorted({s for srcs in feature_sources.values() for s in srcs})
    edges, urns_by_key, _ = _collect_upstream_graph(graph, all_sources)

    tags_by_table: dict[str, set[str]] = {}
    for key, urns in urns_by_key.items():
        tags: set[str] = set()
        for urn in urns:
            gt = graph.get_aspect(urn, GlobalTagsClass)
            tags.update(t.tag for t in (gt.tags if gt else []))
        tags_by_table[key] = tags

    sql_by_key: dict[str, str] = {}
    for key, urns in urns_by_key.items():
        for urn in urns:
            sql = sdk_read.view_logic(graph, urn)
            if sql:
                sql_by_key[key] = sql
                break

    report = AuditReport(model_urn=model_urn, label_column=label_column,
                         event_ts_column=event_ts_column)
    for name, sources in sorted(feature_sources.items()):
        src_key = table_key(sources[0]) if sources else ""
        closure, paths = upstream_closure(edges, (src_key, name))
        snippet = sql_snippet_for(sql_by_key.get(src_key, ""), name)
        verdict = evaluate_feature(
            name, closure, tags_by_table, label_column, event_ts_column, snippet, paths
        )
        if verdict.verdict == "SUSPECT":
            verdict = _llm_confirm(verdict, event_ts_column)
        report.verdicts.append(verdict)

    payload = "|".join([model_urn] + sorted(v.feature for v in report.leaks + report.suspects))
    report.audit_hash = hashlib.sha256(f"leakage|{payload}".encode()).hexdigest()
    return report


def _llm_confirm(verdict: FeatureVerdict, event_ts_column: str) -> FeatureVerdict:
    """Optional LLM pass on L3 suspects; absence of a provider changes nothing."""
    from agent.adapters import llm

    answer = llm.complete(
        f"Does this SQL aggregate data from AFTER the `{event_ts_column}` timestamp of the row "
        f"being scored (target leakage), or only from before it? Answer LEAK or OK, then one "
        f"sentence.\n\n```sql\n{verdict.sql_snippet}\n```",
        max_tokens=120,
    )
    if answer:
        if answer.strip().upper().startswith("LEAK"):
            verdict.verdict = "LEAK"
            verdict.reason += f" — LLM confirmed: {answer.strip()[:160]}"
        else:
            verdict.reason += f" — LLM assessment: {answer.strip()[:160]}"
    return verdict


def render_markdown(report: AuditReport) -> str:
    lines = [
        "\n\n---\n## 🧬 Leakage audit (Blast Radius)",
        f"Structural audit of `{report.model_urn.split(',')[1]}` — label `{report.label_column}`, "
        f"prediction time `{report.event_ts_column}`. Rules: L1 direct label, L2 post-outcome "
        "lineage, L3 forward-looking SQL. Lineage-evidence based, not a statistical guarantee.",
        "",
        "| feature | verdict | rule | why |",
        "|---|---|---|---|",
    ]
    for v in report.verdicts:
        icon = {"LEAK": "🔴", "SUSPECT": "🟡", "CLEAN": "🟢"}[v.verdict]
        lines.append(f"| `{v.feature}` | {icon} {v.verdict} | {v.rule or '—'} | {v.reason or 'no leakage signal'} |")
    for v in report.leaks + report.suspects:
        if v.path:
            lines.append(f"\n**{v.feature} path:** `{' → '.join(v.path)}`")
        if v.sql_snippet:
            lines.append(f"```sql\n{v.sql_snippet}\n```")
        lines.append(
            f"_Remediation:_ recompute `{v.feature}` using only data available at "
            f"`{report.event_ts_column}`, or drop it and retrain."
        )
    lines.append(f"\n`evidence_hash: {report.audit_hash}`")
    return "\n".join(lines)
