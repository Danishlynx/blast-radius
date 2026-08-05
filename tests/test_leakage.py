"""Table-driven tests for the leakage rules (pure core)."""

import pytest

from agent.leakage import (
    POST_OUTCOME_TAG,
    evaluate_feature,
    forward_looking,
    sql_snippet_for,
    upstream_closure,
)

FCT = "warehouse.main.fct_customer_features"
STG_CB = "warehouse.main.stg_chargebacks"
CB = "warehouse.main.chargebacks"
LABELS = "warehouse.main.labels"


def test_l2_post_outcome_lineage_is_leak():
    # chargebacks_next_30d's closure crosses the post-outcome chargebacks table
    verdict = evaluate_feature(
        "chargebacks_next_30d",
        upstream_columns={(STG_CB, "chargeback_ts"), (CB, "chargeback_ts")},
        tags_by_table={CB: {POST_OUTCOME_TAG}, STG_CB: {POST_OUTCOME_TAG}},
        label_column="is_fraud",
        event_ts_column="event_ts",
        sql_snippet="cb.chargeback_ts < t.event_ts + interval 30 day",
    )
    assert verdict.verdict == "LEAK" and verdict.rule == "L2"


def test_l1_label_column_is_leak():
    verdict = evaluate_feature(
        "label_echo",
        upstream_columns={(LABELS, "is_fraud")},
        tags_by_table={},
        label_column="is_fraud",
        event_ts_column="event_ts",
        sql_snippet="",
    )
    assert verdict.verdict == "LEAK" and verdict.rule == "L1"


def test_l3_forward_window_is_suspect():
    verdict = evaluate_feature(
        "future_spend",
        upstream_columns={("warehouse.main.stg_transactions", "amount_usd")},
        tags_by_table={},
        label_column="is_fraud",
        event_ts_column="event_ts",
        sql_snippet="where t2.event_ts < t.event_ts + interval 7 day",
    )
    assert verdict.verdict == "SUSPECT" and verdict.rule == "L3"


@pytest.mark.parametrize("feature,cols,sql", [
    # honest trailing-window features stay clean
    ("avg_amount_30d", {("warehouse.main.stg_transactions", "amount_usd")},
     "and t2.event_ts > t.event_ts - interval 30 day"),
    ("txn_count_30d", {("warehouse.main.stg_transactions", "txn_id")},
     "and t2.event_ts <= t.event_ts"),
    ("country_risk", {("warehouse.main.stg_transactions", "country")}, "case t.country when 'US'"),
])
def test_honest_features_clean(feature, cols, sql):
    verdict = evaluate_feature(
        feature, cols, {}, "is_fraud", "event_ts", sql,
    )
    assert verdict.verdict == "CLEAN", verdict


def test_forward_looking_needs_event_ts_reference():
    assert not forward_looking("select a + interval 3 day from t", "event_ts")
    assert forward_looking("event_ts + interval 3 day", "event_ts")
    assert not forward_looking("event_ts - interval 30 day", "event_ts")


def test_upstream_closure_walks_multiple_hops():
    edges = [
        (FCT, "chargebacks_next_30d", STG_CB, "chargeback_ts"),
        (STG_CB, "chargeback_ts", CB, "chargeback_ts"),
    ]
    closure, paths = upstream_closure(edges, (FCT, "chargebacks_next_30d"))
    assert (CB, "chargeback_ts") in closure
    assert paths[(CB, "chargeback_ts")][0].startswith(FCT)


def test_sql_snippet_extraction():
    sql = "\n".join(f"line {i}" for i in range(10)) + "\nselect chargebacks_next_30d\nmore"
    snip = sql_snippet_for(sql, "chargebacks_next_30d")
    assert "chargebacks_next_30d" in snip
    assert sql_snippet_for(sql, "not_present") == ""
