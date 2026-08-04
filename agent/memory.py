"""Remember: the metadata graph is the agent's only memory.

Day 3 ships the evidence hash — the stable identity of "this change hitting
these models" that makes re-runs idempotent. Day 4 adds the incident lookup
(dedupe against DataHub) and the structured-property run log.
"""

from __future__ import annotations

import hashlib

from agent.models import BlastRadius, ChangeEvent


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
