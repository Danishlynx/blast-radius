"""Table-driven tests for the schema-diff classifier."""

import pytest

from agent.sentinel import diff_schemas

V1 = [("txn_id", "VARCHAR"), ("amount_usd", "DOUBLE"), ("event_ts", "TIMESTAMP")]


@pytest.mark.parametrize(
    ("before", "after", "expected_type", "expected_cols"),
    [
        # the poison migration: rename + unit/type change
        (
            V1,
            [("txn_id", "VARCHAR"), ("amount", "BIGINT"), ("event_ts", "TIMESTAMP")],
            "rename",
            [("amount_usd", "amount")],
        ),
        # dropped column
        (
            V1,
            [("txn_id", "VARCHAR"), ("event_ts", "TIMESTAMP")],
            "drop",
            [("amount_usd", None)],
        ),
        # additive (cosmetic) change
        (
            V1,
            V1 + [("merchant_id", "VARCHAR")],
            "add",
            [(None, "merchant_id")],
        ),
        # pure type change, names intact
        (
            V1,
            [("txn_id", "VARCHAR"), ("amount_usd", "BIGINT"), ("event_ts", "TIMESTAMP")],
            "type_change",
            [("amount_usd", "amount_usd")],
        ),
        # no change
        (V1, V1, "other", []),
    ],
)
def test_diff_schemas(before, after, expected_type, expected_cols):
    change_type, columns = diff_schemas(before, after)
    assert change_type == expected_type
    got = [(c.before, c.after) for c in columns]
    assert got == expected_cols


def test_rename_carries_types():
    _, columns = diff_schemas(
        [("amount_usd", "DOUBLE")], [("amount", "BIGINT")]
    )
    assert columns[0].type_before == "DOUBLE"
    assert columns[0].type_after == "BIGINT"
