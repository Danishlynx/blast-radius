"""Thin MCP mutation adapter (tags, doc append). Zero business logic.

Uses the same mcp-server-datahub session as reads, with mutation tools
enabled. Callers handle failure by falling back to the SDK writer.
"""

from __future__ import annotations

from agent.adapters.mcp_read import McpDataHub, run_sync


async def _add_tag(entity_urns: list[str], tag_urn: str) -> None:
    async with McpDataHub(mutations=True) as dh:
        await dh.call("add_tags", entity_urns=entity_urns, tag_urns=[tag_urn])


async def _remove_tag(entity_urns: list[str], tag_urn: str) -> None:
    async with McpDataHub(mutations=True) as dh:
        await dh.call("remove_tags", entity_urns=entity_urns, tag_urns=[tag_urn])


async def _append_description(entity_urn: str, text: str) -> None:
    async with McpDataHub(mutations=True) as dh:
        await dh.call(
            "update_description", entity_urn=entity_urn, description=text, operation="append"
        )


def add_tag(entity_urns: list[str], tag_urn: str) -> None:
    run_sync(_add_tag(entity_urns, tag_urn))


def remove_tag(entity_urns: list[str], tag_urn: str) -> None:
    run_sync(_remove_tag(entity_urns, tag_urn))


def append_description(entity_urn: str, text: str) -> None:
    run_sync(_append_description(entity_urn, text))
