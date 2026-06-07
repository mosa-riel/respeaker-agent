"""Tool registry — the agent's callable tools in OpenAI/MCP function shape.

Phase 4 fans real tools in from MCP servers; for now this holds a couple of
built-in demo tools so the multi-tool loop is exercisable end-to-end. Each tool
exposes a JSON-schema `parameters` block and an async `handler`. Dispatch runs the
handler and returns its real result — the loop feeds that back to the model rather
than trusting the model's claim that it ran (the 'verify, don't trust' rule).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments object
    handler: Callable[[dict[str, Any]], Awaitable[Any]]

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    async def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}
        return await tool.handler(args)


# ── built-in demo tools (replaced/augmented by MCP tools in phase 4) ──────────

async def _get_time(_: dict[str, Any]) -> dict[str, Any]:
    return {"iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "epoch": int(time.time())}


async def _echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args.get("text", "")}


def demo_registry() -> ToolRegistry:
    return ToolRegistry([
        Tool(
            name="get_time",
            description="Get the current local date and time.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_get_time,
        ),
        Tool(
            name="echo",
            description="Echo back the given text. Useful only for testing the tool loop.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to echo"}},
                "required": ["text"],
            },
            handler=_echo,
        ),
    ])
