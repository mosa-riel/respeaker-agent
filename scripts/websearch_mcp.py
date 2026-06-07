"""Websearch — a tiny MCP server that does DuckDuckGo web search (no API key).

A tool source for finding places, looking up current info, etc. Returns title/url/
snippet that the agent uses to answer. Pure stdlib + ddgs, stdio transport.

Add to config.json mcp_servers (gitignored):
    { "name": "websearch", "command": "uv",
      "args": ["run", "python", "scripts/websearch_mcp.py"], "enabled": true }
then restart. By voice: "zoek de beste pizzeria in Utrecht".
"""

from __future__ import annotations

import asyncio

from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("websearch")


@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Zoek op het web (DuckDuckGo) naar actuele informatie, plekken, feiten, enz.
    Geeft een lijst met {title, url, snippet} terug om je antwoord op te baseren."""

    def _run() -> list[dict]:
        n = max(1, min(max_results, 8))
        with DDGS() as ddgs:
            return [
                {"title": r.get("title"), "url": r.get("href"), "snippet": (r.get("body") or "")[:300]}
                for r in ddgs.text(query, max_results=n)
            ]

    try:
        return await asyncio.to_thread(_run)
    except Exception as err:  # noqa: BLE001 - surface as a tool result, not a crash
        return [{"error": str(err)[:200]}]


if __name__ == "__main__":
    mcp.run()  # stdio transport
