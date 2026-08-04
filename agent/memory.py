"""Remember: the metadata graph is the agent's only memory.

The evidence hash is the stable identity of "this change hitting these
models". Before acting, the agent asks DataHub for its own prior incidents
(matched by that hash embedded in the incident description) and updates
instead of duplicating. After acting, it writes a run record as a structured
property on the primary affected asset. No local state anywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from datahub.ingestion.graph.client import DataHubGraph

from agent.adapters import graphql, sdk_write
from agent.models import BlastRadius, ChangeEvent

_HASH_RE = re.compile(r"evidence_hash[:=]\s*`?([0-9a-f]{64})`?")


def normalized_change(change: ChangeEvent) -> str:
    """Canonical string for a change, independent of when/how it was seen."""
    cols = sorted(
        f"{c.before or ''}->{c.after or ''}:{c.type_before or ''}->{c.type_after or ''}"
        for c in change.columns
    )
    return f"{change.change_type}|{';'.join(cols)}"


def evidence_hash(change: ChangeEvent, radius: BlastRadius) -> str:
    """sha256(entity_urn + normalized change + sorted affected model urns).

    Two detections of the same break on the same supply chain hash the same,
    regardless of trigger (poller/kafka/cli), timing, or event id.
    """
    model_urns = sorted(n.urn for n in radius.models)
    payload = "|".join([change.entity_urn, normalized_change(change), ",".join(model_urns)])
    return hashlib.sha256(payload.encode()).hexdigest()


def hash_in_description(description: str) -> str | None:
    m = _HASH_RE.search(description or "")
    return m.group(1) if m else None


def find_existing_incident(target_urns: list[str], ehash: str) -> dict | None:
    """The agent's memory lookup: an ACTIVE incident carrying this evidence
    hash on any of the target datasets, or None."""
    for urn in target_urns:
        for incident in graphql.active_incidents(urn):
            if hash_in_description(incident.get("description", "")) == ehash:
                return incident
    return None


def write_run_log(
    graph: DataHubGraph,
    dataset_urn: str,
    *,
    run_id: str,
    ehash: str,
    severity: str,
    verdict: str,
    actions: list[str],
) -> dict:
    record = {
        "run_id": run_id,
        "evidence_hash": ehash,
        "severity": severity,
        "verdict": verdict,
        "actions": actions,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    sdk_write.set_run_log(graph, dataset_urn, json.dumps(record))
    return record
