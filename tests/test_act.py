"""Unit tests for the act stage's pure pieces."""

from agent.act import incident_description, incident_title, planned_actions, primary_target
from agent.diagnose import Diagnosis
from agent.memory import hash_in_description
from agent.models import BlastNode, BlastRadius, ChangeEvent, ColumnChange

RAW = "urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)"
FCT = "urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.fct_customer_features,PROD)"
FCT_DBT = "urn:li:dataset:(urn:li:dataPlatform:dbt,warehouse.main.fct_customer_features,PROD)"


def make_change() -> ChangeEvent:
    return ChangeEvent(
        source="cli",
        entity_urn=RAW,
        change_type="rename",
        columns=[ColumnChange(before="amount_usd", after="amount",
                              type_before="DOUBLE", type_after="BIGINT")],
    )


def make_radius(change) -> BlastRadius:
    return BlastRadius(
        change=change,
        nodes=[
            BlastNode(urn=FCT_DBT, entity_type="dataset", hop=4),
            BlastNode(urn=FCT, entity_type="dataset", hop=5),
            BlastNode(urn="urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_model_1,PROD)",
                      entity_type="mlModel", hop=7, deployed=True, env="PROD"),
        ],
        paths=[[f"{RAW}.amount_usd", f"{FCT}.avg_amount_30d"]],
    )


def make_diag(change, radius) -> Diagnosis:
    from agent.diagnose import build_evidence

    return Diagnosis(
        verdict="breaking",
        severity="P0",
        rationale="a renamed column is referenced by downstream SQL",
        evidence=build_evidence(change, radius, [], "breaking", "P0"),
    )


def test_planned_actions_matrix():
    assert planned_actions("P0") == ["incident", "tag", "doc", "alert"]
    assert planned_actions("P1") == ["incident", "tag", "doc", "alert"]
    assert planned_actions("P2") == ["incident", "tag", "doc"]
    assert planned_actions("P3") == ["doc"]


def test_primary_target_is_deepest_physical_dataset():
    change = make_change()
    assert primary_target(change, make_radius(change)) == FCT  # not the dbt sibling


def test_description_carries_recoverable_evidence_hash():
    change = make_change()
    radius = make_radius(change)
    diag = make_diag(change, radius)
    desc = incident_description(change, radius, diag)
    assert hash_in_description(desc) == diag.evidence["evidence_hash"]
    assert "amount_usd" in desc and "P0" in desc and "evidence JSON" in desc


def test_title_names_the_change():
    change = make_change()
    diag = make_diag(change, make_radius(change))
    title = incident_title(change, diag)
    assert "P0" in title and "amount_usd" in title and "amount" in title
