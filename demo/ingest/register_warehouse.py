"""Register physical DuckDB table schemas in DataHub.

The dbt source emits schemas on the dbt-platform entities but leaves the
duckdb sibling datasets schema-less. This script live-introspects the
warehouse (like a warehouse connector would) and emits schemaMetadata for
every table's duckdb URN. Re-running after a migration lands the new schema
version — which is exactly what the sentinel diffs against.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
from datahub.emitter.mce_builder import make_data_platform_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.metadata.schema_classes import (
    BooleanTypeClass,
    DateTypeClass,
    NumberTypeClass,
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    TimeTypeClass,
)
from dotenv import load_dotenv

DB_PATH = Path(__file__).resolve().parents[1] / "warehouse.duckdb"


def field_type(duck_type: str) -> SchemaFieldDataTypeClass:
    t = duck_type.upper()
    if t.startswith(("VARCHAR", "TEXT", "CHAR", "UUID")):
        return SchemaFieldDataTypeClass(type=StringTypeClass())
    if t.startswith("BOOLEAN"):
        return SchemaFieldDataTypeClass(type=BooleanTypeClass())
    if t.startswith("TIMESTAMP"):
        return SchemaFieldDataTypeClass(type=TimeTypeClass())
    if t.startswith("DATE"):
        return SchemaFieldDataTypeClass(type=DateTypeClass())
    return SchemaFieldDataTypeClass(type=NumberTypeClass())


load_dotenv()

def main() -> int:
    graph = DataHubGraph(
        DatahubClientConfig(
            server=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.environ.get("DATAHUB_TOKEN") or None,
        )
    )
    con = duckdb.connect(str(DB_PATH), read_only=True)
    tables = [r[0] for r in con.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'main' order by table_name"
    ).fetchall()]

    for table in tables:
        cols = con.execute(f"describe {table}").fetchall()  # (name, type, null, ...)
        fields = [
            SchemaFieldClass(
                fieldPath=name,
                type=field_type(dtype),
                nativeDataType=dtype,
                nullable=True,
            )
            for name, dtype, *_ in cols
        ]
        urn = make_dataset_urn("duckdb", f"warehouse.main.{table}", "PROD")
        schema = SchemaMetadataClass(
            schemaName=f"warehouse.main.{table}",
            platform=make_data_platform_urn("duckdb"),
            version=0,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=fields,
        )
        graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn, aspect=schema))
        print(f"  registered schema: warehouse.main.{table} ({len(fields)} columns)")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
