"""Tool registry — the agent's callable tools in OpenAI/MCP function shape.

Tools come from the MCP servers (`mcp_client`) and the opt-in Bluetooth control
(`bluetooth`). Each tool exposes a JSON-schema `parameters` block and an async `handler`. Dispatch runs the
handler and returns its real result — the loop feeds that back to the model rather
than trusting the model's claim that it ran (the 'verify, don't trust' rule).
"""

from __future__ import annotations

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

