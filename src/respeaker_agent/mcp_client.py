"""MCP client manager — fans tools in from N MCP servers into the agent's registry.

Each enabled server in `settings.mcp_servers` is connected at startup (stdio via
command/args, or remote via http/sse url). Its tools are registered into the shared
`ToolRegistry`, namespaced `{server}__{tool}` to avoid collisions; dispatch routes
back to the owning session via `call_tool`.

Security: stdio servers run a subprocess, so their `command`/`args` come only from
`config.json` (a trusted, hand-edited file) — never from the unauthenticated API.
Secrets (e.g. the HA token) are inherited from the agent's own environment, not
stored in `config.json`. See docs/reference/security.md.

Lifecycle note: contexts are entered on startup and closed on shutdown within the
same task (the FastAPI lifespan), satisfying anyio's same-task cancellation rule.
Adding/removing a server via the API rewrites config and requires a restart to
connect — MCP sessions are not hot-swapped from a request task.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import McpServer, Settings
from .tools import Tool, ToolRegistry
from .trace import TraceBus

_LOGGER = logging.getLogger(__name__)


class McpManager:
    def __init__(self, settings: Settings, trace: TraceBus, registry: ToolRegistry) -> None:
        self._settings = settings
        self._trace = trace
        self._registry = registry
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        # server name -> {"connected": bool, "tools": [names], "error": str|None}
        self.status: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        for srv in self._settings.mcp_servers:
            if not srv.enabled:
                self.status[srv.name] = {"connected": False, "tools": [], "error": "disabled"}
                continue
            try:
                await self._connect(srv)
            except Exception as err:  # noqa: BLE001 - one bad server mustn't kill the rest
                msg = _safe(err)
                self.status[srv.name] = {"connected": False, "tools": [], "error": msg}
                self._trace.emit("error", f"MCP '{srv.name}' connect failed: {msg}", level="error")
                _LOGGER.exception("MCP connect failed: %s", srv.name)

    async def stop(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()

    async def call_raw(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        """Call a tool on a connected server directly, regardless of whether it's
        exposed to the agent (used by HomeContext to read the entity list even when
        that tool isn't in the curated set)."""
        session = self._sessions.get(server)
        if session is None:
            raise RuntimeError(f"MCP server not connected: {server}")
        return _result_to_json(await session.call_tool(tool, args))

    async def _connect(self, srv: McpServer) -> None:
        if srv.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(srv.url))
        else:
            params = StdioServerParameters(
                command=srv.command,
                args=list(srv.args),
                # Inherit the agent env (so .env secrets like HOMEASSISTANT_TOKEN reach
                # the subprocess), with the server's non-secret env layered on top.
                env={**os.environ, **srv.env},
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        resp = await session.list_tools()
        enabled = set(srv.enabled_tools)  # empty = all enabled
        all_tools: list[dict[str, Any]] = []  # every tool the server offers (for the UI)
        exposed: list[str] = []
        for t in resp.tools:
            on = (not enabled) or (t.name in enabled)
            all_tools.append({"name": t.name, "description": (t.description or "").split("\n")[0][:140], "enabled": on})
            if not on:
                continue
            qualified = f"{_slug(srv.name)}__{t.name}"
            self._registry.add(self._make_tool(session, qualified, t.name, t.description or "", t.inputSchema or {}))
            exposed.append(qualified)
        self._sessions[srv.name] = session
        self.status[srv.name] = {
            "connected": True, "error": None,
            "tools": exposed, "all_tools": all_tools,
        }
        off_n = len(all_tools) - len(exposed)
        extra = f", {off_n} disabled" if off_n else ""
        self._trace.emit("info", f"MCP '{srv.name}' connected ({len(exposed)} tools{extra})", data={"tools": exposed})

    def _make_tool(self, session: ClientSession, qualified: str, original: str, desc: str, schema: dict) -> Tool:
        # Normalise to a JSON-schema object so providers accept it.
        params = schema if isinstance(schema, dict) and schema.get("type") == "object" else {
            "type": "object", "properties": {}, "required": [],
        }

        async def handler(args: dict[str, Any]) -> Any:
            result = await session.call_tool(original, args)
            return _result_to_json(result)

        return Tool(name=qualified, description=desc, parameters=params, handler=handler)


def _result_to_json(result: Any) -> Any:
    """Flatten an MCP CallToolResult to something JSON-serialisable for the model."""
    parts: list[str] = []
    for c in getattr(result, "content", []) or []:
        text = getattr(c, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(repr(getattr(c, "data", c)))
    out: dict[str, Any] = {"content": "\n".join(parts) if parts else ""}
    structured = getattr(result, "structuredContent", None)
    if structured:
        out["structured"] = structured
    if getattr(result, "isError", False):
        out["isError"] = True
    return out


def _slug(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
