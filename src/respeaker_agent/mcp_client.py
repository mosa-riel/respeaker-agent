"""MCP client manager — fans tools in from N MCP servers into the agent's registry.

Each enabled server in `settings.mcp_servers` is connected at startup (stdio via
command/args, or remote via http/sse url). Its tools are registered into the shared
`ToolRegistry`, namespaced `{server}__{tool}` to avoid collisions; dispatch routes
back to the owning session via `call_tool`.

Security: stdio servers run a subprocess, so their `command`/`args` come only from
`config.json` (a trusted, hand-edited file) — never from the unauthenticated API.
Secrets (e.g. the HA token) are inherited from the agent's own environment, not
stored in `config.json`. See docs/reference/security.md.

Lifecycle note: each server's connection (transport client + ClientSession) is owned by
its OWN asyncio task and kept open there until shutdown. This is required for the
streamable-HTTP transport, whose internal anyio task group must be entered AND exited in
the same task — stashing these context managers on a shared AsyncExitStack and unwinding
them piecemeal raises "exit cancel scope in a different task". Tool calls are issued from
other tasks (the agent loop); that's fine — only the context manager enter/exit is
task-bound, not the session method calls. Adding/removing a server via the API rewrites
config and requires a restart to connect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
        self._sessions: dict[str, ClientSession] = {}
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()
        # server name -> {"connected": bool, "tools": [names], "error": str|None}
        self.status: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        # Spawn one task per server; each owns its connection and signals `ready` once it
        # has connected (and registered its tools) or failed. Wait for all to settle so
        # the tool registry is populated before the agent runs — but one bad/slow server
        # can't kill the rest (its task isolates the failure).
        waits = []
        for srv in self._settings.mcp_servers:
            if not srv.enabled:
                self.status[srv.name] = {"connected": False, "tools": [], "error": "disabled"}
                continue
            ready = asyncio.Event()
            self._tasks.append(asyncio.create_task(self._serve(srv, ready), name=f"mcp:{srv.name}"))
            waits.append(ready.wait())
        if waits:
            await asyncio.gather(*waits)

    async def stop(self) -> None:
        self._shutdown.set()  # let each server task exit its `async with` cleanly
        if self._tasks:
            _done, pending = await asyncio.wait(self._tasks, timeout=5)
            for t in pending:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()

    async def _serve(self, srv: McpServer, ready: asyncio.Event) -> None:
        """Own one server's connection for the process lifetime. The full context-manager
        chain is entered and exited in THIS task (anyio requirement for streamable-HTTP)."""
        try:
            if srv.transport == "http":
                from mcp.client.streamable_http import streamablehttp_client

                async with streamablehttp_client(srv.url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await self._register(srv, session)
                        ready.set()
                        await self._shutdown.wait()
            else:
                params = StdioServerParameters(
                    command=srv.command,
                    args=list(srv.args),
                    # Inherit the agent env (so .env secrets like HOMEASSISTANT_TOKEN reach
                    # the subprocess), with the server's non-secret env layered on top.
                    env={**os.environ, **srv.env},
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await self._register(srv, session)
                        ready.set()
                        await self._shutdown.wait()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - one bad server mustn't kill the rest
            msg = _safe(err)
            self.status[srv.name] = {"connected": False, "tools": [], "error": msg}
            self._trace.emit("error", f"MCP '{srv.name}' connect failed: {msg}", level="error")
            _LOGGER.exception("MCP connect failed: %s", srv.name)
        finally:
            ready.set()  # never leave start() waiting, even on early failure

    def sources_summary(self) -> str | None:
        """A compact per-server tool map for the prompt: which functions each MCP
        source offers, so the model picks the right call (and fetches HA entities via
        the tool itself) — instead of injecting the whole entity list every turn."""
        lines = []
        for srv in self._settings.mcp_servers:
            st = self.status.get(srv.name, {})
            if not st.get("connected"):
                continue
            tools = [t.split("__", 1)[-1] for t in st.get("tools", [])]
            if tools:
                lines.append(f"- {srv.name}: {', '.join(tools)}")
        return "\n".join(lines) if lines else None

    async def call_raw(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        """Call a tool on a connected server directly, regardless of whether it's
        exposed to the agent (used by HomeContext to read the entity list even when
        that tool isn't in the curated set)."""
        session = self._sessions.get(server)
        if session is None:
            raise RuntimeError(f"MCP server not connected: {server}")
        return _result_to_json(await session.call_tool(tool, args))

    async def _register(self, srv: McpServer, session: ClientSession) -> None:
        """Initialize the session, fetch its tools, and register the enabled ones."""
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
            overrides = (srv.tool_arg_overrides or {}).get(t.name, {})
            self._registry.add(self._make_tool(session, qualified, t.name, t.description or "", t.inputSchema or {}, overrides))
            exposed.append(qualified)
        self._sessions[srv.name] = session
        self.status[srv.name] = {
            "connected": True, "error": None,
            "tools": exposed, "all_tools": all_tools,
        }
        off_n = len(all_tools) - len(exposed)
        extra = f", {off_n} disabled" if off_n else ""
        self._trace.emit("info", f"MCP '{srv.name}' connected ({len(exposed)} tools{extra})", data={"tools": exposed})

    def _make_tool(self, session: ClientSession, qualified: str, original: str, desc: str, schema: dict, overrides: dict | None = None) -> Tool:
        # Normalise to a JSON-schema object so providers accept it.
        params = schema if isinstance(schema, dict) and schema.get("type") == "object" else {
            "type": "object", "properties": {}, "required": [],
        }
        forced = overrides or {}

        async def handler(args: dict[str, Any]) -> Any:
            # Forced args win over whatever the model passed (e.g. include_hidden=false).
            result = await session.call_tool(original, {**args, **forced})
            return _result_to_json(result)

        return Tool(name=qualified, description=desc, parameters=params, handler=handler)


def _result_to_json(result: Any) -> Any:
    """Flatten an MCP CallToolResult to something compact + JSON-serialisable for the
    model. Sends the data ONCE: prefer the structured object (servers like ha-mcp also
    return it doubly-escaped in the text content — don't ship both). Strips ha-mcp's
    repeated `metadata` noise (timezone/notes) and unwraps a lone `{"data": …}`."""
    is_err = bool(getattr(result, "isError", False))

    structured = getattr(result, "structuredContent", None)
    if structured is None:
        # No structured payload — use the text, parsed back to JSON if possible so it
        # isn't a doubly-escaped string to the model.
        parts = [t for c in (getattr(result, "content", []) or [])
                 if (t := getattr(c, "text", None)) is not None]
        text = "\n".join(parts) if parts else "".join(
            repr(getattr(c, "data", c)) for c in (getattr(result, "content", []) or []))
        try:
            structured = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            out: dict[str, Any] = {"content": text}
            if is_err:
                out["isError"] = True
            return out

    structured = _trim(structured)
    out = {"structured": structured}
    if is_err:
        out["isError"] = True
    return out


def _trim(obj: Any) -> Any:
    """Drop ha-mcp's top-level `metadata` block and unwrap a lone `{"data": …}`."""
    if isinstance(obj, dict):
        if "metadata" in obj and len(obj) > 1:
            obj = {k: v for k, v in obj.items() if k != "metadata"}
        if set(obj.keys()) == {"data"}:
            return obj["data"]
    return obj


def _slug(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)


def _safe(err: BaseException) -> str:
    # streamable-HTTP wraps the real cause (e.g. a DNS/connect error) in an
    # ExceptionGroup from its internal task group — unwrap to the leaf for a useful message.
    while isinstance(err, BaseExceptionGroup) and err.exceptions:
        err = err.exceptions[0]
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
