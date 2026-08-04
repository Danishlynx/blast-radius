"""Act: file the incident, mark the model, alert the owners.

Actions per severity (handoff matrix; the fix PR is Day 5's addition):
  P0/P1  incident + model-at-risk tag + doc append + owner alert
  P2     incident + model-at-risk tag + doc append
  P3     doc note only

Idempotency first: before any write the agent looks up its own prior
incident by evidence hash. A match means "update, don't duplicate" — the
incident is refreshed, no new artifacts, no re-alert.

APPROVAL_REQUIRED_SEVERITIES (comma-sep, e.g. "P0,P1") switches those
severities to plan-only mode: report what would be done, write nothing.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Literal

from datahub.ingestion.graph.client import DataHubGraph
from pydantic import BaseModel

from agent import memory
from agent.adapters import graphql, mcp_mutate, sdk_write, slack
from agent.diagnose import Diagnosis
from agent.models import BlastRadius, ChangeEvent

MODEL_AT_RISK_TAG = "urn:li:tag:model-at-risk"
PRIORITY = {"P0": "CRITICAL", "P1": "HIGH", "P2": "MEDIUM", "P3": "LOW"}
FRONTEND = "http://localhost:9002"


class ActionResult(BaseModel):
    action: str
    status: Literal["done", "updated", "skipped", "failed", "needs-approval"]
    detail: str = ""


class ActReport(BaseModel):
    duplicate_suppressed: bool = False
    incident_urn: str | None = None
    target_urn: str = ""
    results: list[ActionResult] = []
    run_log: dict = {}


def planned_actions(severity: str) -> list[str]:
    if severity in ("P0", "P1"):
        return ["incident", "tag", "doc", "alert"]
    if severity == "P2":
        return ["incident", "tag", "doc"]
    return ["doc"]


def primary_target(change: ChangeEvent, radius: BlastRadius) -> str:
    """The deepest affected physical dataset — the feature table in the demo."""
    datasets = [
        n for n in radius.nodes if n.entity_type == "dataset" and ":dataPlatform:dbt" not in n.urn
    ]
    if datasets:
        return max(datasets, key=lambda n: n.hop).urn
    return change.entity_urn


def incident_title(change: ChangeEvent, diag: Diagnosis) -> str:
    col = change.columns[0] if change.columns else None
    what = f"{col.before or '?'} → {col.after or 'removed'}" if col else change.change_type
    return f"[{diag.severity}] upstream {change.change_type}: {what} breaks ML supply chain"


def incident_description(change: ChangeEvent, radius: BlastRadius, diag: Diagnosis) -> str:
    col_lines = "\n".join(
        f"- `{c.before or '∅'}` ({c.type_before or '?'}) → `{c.after or '∅'}` ({c.type_after or '?'})"
        for c in change.columns
    )
    path_lines = "\n".join(f"- `{' → '.join(p)}`" for p in radius.paths) or "- (table-level)"
    sql_lines = "\n".join(
        f"- **{r.asset.split(',')[1]}** — {r.reason}\n  ```sql\n  {r.snippet}\n  ```"
        for r in diag.sql_refs
    )
    model_lines = "\n".join(
        f"- `{n.urn}` deployed={bool(n.deployed)} env={n.env or '-'}" for n in radius.models
    )
    llm_section = f"\n**Semantic assessment:** {diag.llm_note}\n" if diag.llm_note else ""
    return f"""## 🚨 Blast Radius: {diag.verdict} change upstream of production ML

**Severity {diag.severity}** — {diag.rationale}. Detected via `{change.source}` at {change.detected_at.isoformat(timespec='seconds')}.

### The change ({change.change_type} on `{change.entity_urn.split(',')[1]}`)
{col_lines}
{llm_section}
### Column flow (confidence: {radius.confidence})
{path_lines}

### Captured SQL evidence
{sql_lines or '- none'}

### Affected models
{model_lines or '- none'}

### Owners notified
{', '.join(f'`{o}`' for o in radius.all_owners) or '- none on file'}

---
Filed automatically by [Blast Radius](https://github.com/Danishlynx/blast-radius). Re-runs update this incident in place.

`evidence_hash: {diag.evidence['evidence_hash']}`

<details><summary>evidence JSON</summary>

```json
{json.dumps(diag.evidence, indent=2)}
```
</details>
"""


def _entity_link(urn: str, kind: str = "dataset") -> str:
    return f"{FRONTEND}/{kind}/{urllib.parse.quote(urn, safe='')}"


def _alert_blocks(change: ChangeEvent, radius: BlastRadius, diag: Diagnosis, incident_urn: str) -> tuple[list[dict], str]:
    col = change.columns[0] if change.columns else None
    what = f"`{col.before}` → `{col.after or 'removed'}`" if col else change.change_type
    text = (
        f"{'🔴' if diag.severity in ('P0', 'P1') else '🟡'} *Blast Radius {diag.severity}* — "
        f"{diag.verdict} {change.change_type} upstream of production ML\n"
        f"*Change:* {what} on {change.entity_urn.split(',')[1]}\n"
        f"*Impact:* {len(radius.models)} model(s), {len(radius.deployments)} PROD deployment(s)\n"
        f"*Diagnosis:* {diag.rationale}\n"
        f"*Incident:* {_entity_link(primary_targets_link(radius, change))}"
    )
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Blast Radius {diag.severity}: ML supply chain at risk"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]
    return blocks, text


def primary_targets_link(radius: BlastRadius, change: ChangeEvent) -> str:
    return primary_target(change, radius)


def act(graph: DataHubGraph, change: ChangeEvent, radius: BlastRadius, diag: Diagnosis) -> ActReport:
    report = ActReport()
    actions = planned_actions(diag.severity)
    ehash = diag.evidence["evidence_hash"]
    target = primary_target(change, radius)
    report.target_urn = target
    targets = [target] + ([change.entity_urn] if change.entity_urn != target else [])

    approval_gate = {
        s.strip() for s in os.environ.get("APPROVAL_REQUIRED_SEVERITIES", "").split(",") if s.strip()
    }
    if diag.severity in approval_gate:
        report.results = [
            ActionResult(action=a, status="needs-approval", detail="APPROVAL_REQUIRED_SEVERITIES")
            for a in actions
        ]
        return report

    # ---- Remember, before acting: is this break already known to the graph?
    existing = memory.find_existing_incident(targets, ehash) if "incident" in actions else None
    if existing:
        graphql.update_incident(
            existing["urn"], incident_title(change, diag), incident_description(change, radius, diag)
        )
        report.duplicate_suppressed = True
        report.incident_urn = existing["urn"]
        report.results.append(
            ActionResult(
                action="incident",
                status="updated",
                detail=f"duplicate suppressed — updated {existing['urn']}",
            )
        )
    elif "incident" in actions:
        try:
            urn = graphql.raise_incident(
                targets,
                incident_title(change, diag),
                incident_description(change, radius, diag),
                priority=PRIORITY[diag.severity],
            )
            report.incident_urn = urn
            report.results.append(ActionResult(action="incident", status="done", detail=urn))
        except Exception as exc:
            report.results.append(ActionResult(action="incident", status="failed", detail=str(exc)[:200]))

    model_urns = [n.urn for n in radius.models]
    if "tag" in actions and model_urns and not report.duplicate_suppressed:
        sdk_write.ensure_tag(
            graph, MODEL_AT_RISK_TAG, "model-at-risk",
            "An upstream change puts this model's inputs at risk. Applied by Blast Radius.",
            "#d0021b",
        )
        try:
            mcp_mutate.add_tag(model_urns, MODEL_AT_RISK_TAG)
            report.results.append(ActionResult(action="tag", status="done", detail="via MCP"))
        except Exception:
            for m in model_urns:
                sdk_write.add_tag(graph, m, MODEL_AT_RISK_TAG)
            report.results.append(ActionResult(action="tag", status="done", detail="via SDK fallback"))

    if "doc" in actions and model_urns and not report.duplicate_suppressed:
        note = (
            f"\n\n---\n⚠️ **Blast Radius {diag.severity}** ({change.detected_at.date()}): "
            f"{diag.rationale}. Evidence `{ehash[:8]}`."
        )
        try:
            mcp_mutate.append_description(model_urns[0], note)
            report.results.append(ActionResult(action="doc", status="done", detail="via MCP"))
        except Exception:
            sdk_write.append_model_description(graph, model_urns[0], note)
            report.results.append(ActionResult(action="doc", status="done", detail="via SDK fallback"))

    if "alert" in actions and not report.duplicate_suppressed:
        blocks, text = _alert_blocks(change, radius, diag, report.incident_urn or "")
        try:
            channel = slack.alert(blocks, text)
            report.results.append(ActionResult(action="alert", status="done", detail=channel))
        except Exception as exc:
            report.results.append(ActionResult(action="alert", status="failed", detail=str(exc)[:200]))

    # ---- Remember, after acting: leave the run record in the graph.
    report.run_log = memory.write_run_log(
        graph, target,
        run_id=change.event_id, ehash=ehash, severity=diag.severity,
        verdict=diag.verdict,
        actions=[f"{r.action}:{r.status}" for r in report.results],
    )
    return report
