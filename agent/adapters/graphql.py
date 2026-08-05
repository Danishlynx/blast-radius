"""Thin GraphQL adapter for DataHub incidents. Zero business logic.

Incidents are not exposed through the MCP server (verified v0.6.0), so this
is the one surface the agent talks to over raw GraphQL. Shapes verified by
introspection against DataHub v1.6.0.
"""

from __future__ import annotations

import os
from typing import Any

import requests


def _gql(query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        f"{os.environ.get('DATAHUB_GMS_URL', 'http://localhost:8080')}/api/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {os.environ.get('DATAHUB_TOKEN', '')}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL error: {body['errors']}")
    return body["data"]


def raise_incident(
    resource_urns: list[str],
    title: str,
    description: str,
    priority: str = "HIGH",
    incident_type: str = "DATA_SCHEMA",
    custom_type: str | None = None,
) -> str:
    """Returns the new incident URN. type=CUSTOM requires custom_type."""
    payload: dict[str, Any] = {
        "type": incident_type,
        "title": title,
        "description": description,
        "resourceUrns": resource_urns,
        "priority": priority,
    }
    if custom_type:
        payload["customType"] = custom_type
    data = _gql(
        """
        mutation raiseIncident($input: RaiseIncidentInput!) {
          raiseIncident(input: $input)
        }
        """,
        {"input": payload},
    )
    return data["raiseIncident"]


def update_incident(urn: str, title: str, description: str) -> bool:
    data = _gql(
        """
        mutation updateIncident($urn: String!, $input: UpdateIncidentInput!) {
          updateIncident(urn: $urn, input: $input)
        }
        """,
        {"urn": urn, "input": {"title": title, "description": description}},
    )
    return bool(data["updateIncident"])


def resolve_incident(urn: str, message: str = "resolved") -> bool:
    data = _gql(
        """
        mutation updateIncidentStatus($urn: String!, $input: IncidentStatusInput!) {
          updateIncidentStatus(urn: $urn, input: $input)
        }
        """,
        {"urn": urn, "input": {"state": "RESOLVED", "message": message}},
    )
    return bool(data["updateIncidentStatus"])


def active_incidents(dataset_urn: str) -> list[dict[str, Any]]:
    """ACTIVE incidents on one dataset: [{urn, title, description, created}]."""
    data = _gql(
        """
        query incidents($urn: String!) {
          dataset(urn: $urn) {
            incidents(state: ACTIVE, start: 0, count: 50) {
              total
              incidents { urn title description created { time } }
            }
          }
        }
        """,
        {"urn": dataset_urn},
    )
    entity = data.get("dataset") or {}
    result = entity.get("incidents") or {}
    return result.get("incidents") or []
