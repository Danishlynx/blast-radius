"""Thin client for mcp-server-datahub (stdio). Zero business logic.

Spawns the pinned server (a dev dependency, so `uv run mcp-server-datahub`
resolves from the lockfile) with credentials from the environment, verifies
the tool list at startup, and exposes a generic `call(tool, **args)`.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpDataHub:
    """Async context manager around one MCP server session."""

    def __init__(self, mutations: bool = False):
        self._mutations = mutations
        self._session: ClientSession | None = None
        self._stack: Any = None
        self.tool_names: list[str] = []

    async def __aenter__(self) -> McpDataHub:
        from contextlib import AsyncExitStack

        params = StdioServerParameters(
            command="uv",
            args=["run", "mcp-server-datahub"],
            env={
                **os.environ,
                "DATAHUB_GMS_URL": os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
                "DATAHUB_GMS_TOKEN": os.environ.get("DATAHUB_TOKEN", ""),
                "TOOLS_IS_MUTATION_ENABLED": "true" if self._mutations else "false",
                "LOGURU_LEVEL": "WARNING",
            },
        )
        self._stack = AsyncExitStack()
        errlog = open(os.devnull, "w")  # the server logs chattily to stderr
        self._stack.callback(errlog.close)
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        self.tool_names = [t.name for t in (await self._session.list_tools()).tools]
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def call(self, tool: str, **args: Any) -> Any:
        if tool not in self.tool_names:
            raise RuntimeError(f"MCP tool {tool!r} not available (have: {self.tool_names})")
        result = await self._session.call_tool(tool, args)
        if result.isError:
            raise RuntimeError(f"MCP tool {tool} failed: {result.content}")
        if result.structuredContent is not None:
            return result.structuredContent
        text = "".join(c.text for c in result.content if getattr(c, "text", None))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def run_sync(coro: Any) -> Any:
    """Run an async adapter flow from synchronous CLI code."""
    return asyncio.run(coro)
