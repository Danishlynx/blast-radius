"""Unit tests for traverse's URN parsing helpers."""

from agent.traverse import parse_schema_field, table_key

DS = "urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)"
DS_DBT = "urn:li:dataset:(urn:li:dataPlatform:dbt,warehouse.main.raw_transactions,PROD)"


def test_table_key_normalizes_siblings():
    assert table_key(DS) == table_key(DS_DBT) == "warehouse.main.raw_transactions"


def test_parse_schema_field():
    sf = f"urn:li:schemaField:({DS},amount_usd)"
    assert parse_schema_field(sf) == (DS, "amount_usd")


def test_parse_schema_field_rejects_other_urns():
    assert parse_schema_field(DS) is None
