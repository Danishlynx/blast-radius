"""Diagnose: turn a change + blast radius into a verdict, severity, and
evidence chain.

The core is pure, deterministic logic (unit-tested):
- pull the captured SQL of every affected transformation and find real
  references to the changed columns
- classify: breaking (renamed/dropped/type-narrowed column is referenced),
  semantic (referenced, type-compatible, meaning may have shifted),
  cosmetic (unreferenced or additive)
- score severity P0-P3 from the matrix below

An optional LLM pass (adapters/llm.py) enriches the semantic assessment —
e.g. "values are now cents, not dollars" — and is skipped cleanly when no
provider is configured.

Severity matrix:
  P0  breaking + referenced + deployed model with env=PROD downstream
  P1  breaking + referenced + deployed (non-prod), OR semantic with PROD
  P2  breaking/semantic + referenced, models downstream but none deployed
  P3  cosmetic/additive or unreferenced
"""

from __future__ import annotations

import re
from typing import Literal

from datahub.ingestion.graph.client import DataHubGraph
from pydantic import BaseModel

from agent.adapters import sdk_read
from agent.memory import evidence_hash
from agent.models import BlastRadius, ChangeEvent

Verdict = Literal["breaking", "semantic", "cosmetic"]
Severity = Literal["P0", "P1", "P2", "P3"]


class SqlRef(BaseModel):
    asset: str  # dataset urn
    snippet: str
    reason: str


class Diagnosis(BaseModel):
    verdict: Verdict
    severity: Severity
    rationale: str
    sql_refs: list[SqlRef] = []
    llm_note: str | None = None
    evidence: dict = {}


def find_sql_refs(graph: DataHubGraph, radius: BlastRadius, changed_cols: set[str]) -> list[SqlRef]:
    """Search the captured SQL of affected datasets for the changed columns."""
    refs: list[SqlRef] = []
    patterns = {c: re.compile(rf"\b{re.escape(c)}\b") for c in changed_cols if c}
    for node in radius.nodes:
        if node.entity_type != "dataset":
            continue
        sql = sdk_read.view_logic(graph, node.urn)
        if not sql:
            continue
        for col, pat in patterns.items():
            lines = [ln.strip() for ln in sql.splitlines() if pat.search(ln)]
            if lines:
                refs.append(
                    SqlRef(
                        asset=node.urn,
                        snippet="\n".join(lines[:3]),
                        reason=f"references changed column {col}",
                    )
                )
    return refs


def classify(change: ChangeEvent, referenced: bool) -> tuple[Verdict, str]:
    if change.change_type in ("rename", "drop"):
        if referenced:
            what = "renamed" if change.change_type == "rename" else "dropped"
            return "breaking", f"a {what} column is referenced by downstream SQL"
        return "cosmetic", "changed column is not referenced downstream"
    if change.change_type == "type_change":
        if referenced:
            return "semantic", "referenced column changed type — meaning may have shifted"
        return "cosmetic", "type change on an unreferenced column"
    if change.change_type == "add":
        return "cosmetic", "additive change"
    return "cosmetic", "no structural impact detected"


def score(verdict: Verdict, referenced: bool, radius: BlastRadius) -> Severity:
    deployed_envs = {n.env for n in radius.models if n.deployed}
    deployed_envs |= {n.env for n in radius.deployments}
    deployed_prod = "PROD" in deployed_envs
    deployed_any = any(n.deployed for n in radius.models) or bool(radius.deployments)
    models_exist = bool(radius.models)

    if verdict == "breaking" and referenced and deployed_prod:
        return "P0"
    if (verdict == "breaking" and referenced and deployed_any) or (
        verdict == "semantic" and deployed_prod
    ):
        return "P1"
    if verdict in ("breaking", "semantic") and referenced and models_exist:
        return "P2"
    return "P3"


def build_evidence(
    change: ChangeEvent,
    radius: BlastRadius,
    sql_refs: list[SqlRef],
    verdict: Verdict,
    severity: Severity,
) -> dict:
    return {
        "run_id": change.event_id,
        "evidence_hash": evidence_hash(change, radius),
        "change": change.model_dump(mode="json"),
        "paths": radius.paths,
        "confidence": radius.confidence,
        "sql_refs": [r.model_dump() for r in sql_refs],
        "verdict": verdict,
        "severity": severity,
        "models": [
            {"urn": n.urn, "deployed": bool(n.deployed), "env": n.env} for n in radius.models
        ],
        "deployments": [{"urn": n.urn, "env": n.env} for n in radius.deployments],
        "owners": radius.all_owners,
    }


def diagnose(graph: DataHubGraph, change: ChangeEvent, radius: BlastRadius) -> Diagnosis:
    changed_cols = {c.before for c in change.columns if c.before}
    sql_refs = find_sql_refs(graph, radius, changed_cols)
    referenced = bool(sql_refs)
    verdict, rationale = classify(change, referenced)
    severity = score(verdict, referenced, radius)

    llm_note = None
    if verdict in ("breaking", "semantic"):
        from agent.adapters import llm

        llm_note = llm.semantic_note(change, [r.model_dump() for r in sql_refs])

    return Diagnosis(
        verdict=verdict,
        severity=severity,
        rationale=rationale,
        sql_refs=sql_refs,
        llm_note=llm_note,
        evidence=build_evidence(change, radius, sql_refs, verdict, severity),
    )
