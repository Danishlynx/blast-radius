"""Evidence-hash identity: same break => same hash, regardless of trigger."""

from agent.memory import evidence_hash
from agent.models import BlastNode, BlastRadius, ChangeEvent, ColumnChange

URN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)"
MODEL = BlastNode(urn="urn:li:mlModel:(mlflow,fraud_model_1,PROD)", entity_type="mlModel")


def event(source: str) -> ChangeEvent:
    return ChangeEvent(
        source=source,
        entity_urn=URN,
        change_type="rename",
        columns=[ColumnChange(before="amount_usd", after="amount",
                              type_before="DOUBLE", type_after="BIGINT")],
    )


def test_same_break_same_hash_across_triggers():
    r1 = BlastRadius(change=event("poller"), nodes=[MODEL])
    r2 = BlastRadius(change=event("cli"), nodes=[MODEL])
    assert evidence_hash(event("poller"), r1) == evidence_hash(event("cli"), r2)


def test_different_change_different_hash():
    other = ChangeEvent(
        source="cli", entity_urn=URN, change_type="drop",
        columns=[ColumnChange(before="amount_usd")],
    )
    base = BlastRadius(change=event("cli"), nodes=[MODEL])
    assert evidence_hash(event("cli"), base) != evidence_hash(other, base)


def test_model_set_changes_hash():
    with_model = BlastRadius(change=event("cli"), nodes=[MODEL])
    without_model = BlastRadius(change=event("cli"), nodes=[])
    assert evidence_hash(event("cli"), with_model) != evidence_hash(event("cli"), without_model)
