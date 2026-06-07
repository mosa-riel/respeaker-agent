"""Funbox — a tiny, silly MCP server for testing multi-server tool fan-in.

A second MCP source next to home-assistant: jokes, a magic 8-ball, a coin flip and
dice. Pure stdlib + the mcp SDK, no network, no secrets. Run as a stdio server.

Add to config.json mcp_servers (gitignored):
    { "name": "funbox", "command": "uv",
      "args": ["run", "python", "scripts/funbox_mcp.py"], "enabled": true }
then restart the agent. Try by voice: "vertel een mop" / "kop of munt".
"""

from __future__ import annotations

import random

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("funbox")

_JOKES = [
    "Waarom kunnen skeletten zo slecht liegen? Je kijkt zo door ze heen.",
    "Wat doet een koe op een aardbeving? Een melkshake.",
    "Ik wilde een mop over de tijd vertellen, maar die komt later.",
    "Waarom nam de spin een laptop? Om het web te checken.",
    "Wat is groen en plakt aan de muur? Een boom die niet kan klimmen.",
]
_8BALL = [
    "Zeker weten.", "Vraag het later nog eens.", "Onwaarschijnlijk.",
    "Alle tekenen wijzen op ja.", "Beter van niet.", "Absoluut!",
]


@mcp.tool()
def tell_joke() -> str:
    """Vertel een willekeurige (flauwe) mop."""
    return random.choice(_JOKES)


@mcp.tool()
def magic_8ball(question: str) -> str:
    """Stel een ja/nee-vraag aan de magische 8-ball."""
    return random.choice(_8BALL)


@mcp.tool()
def coin_flip() -> str:
    """Gooi een muntje: kop of munt."""
    return random.choice(["kop", "munt"])


@mcp.tool()
def roll_dice(sides: int = 6) -> int:
    """Gooi een dobbelsteen met het opgegeven aantal zijden (standaard 6)."""
    return random.randint(1, max(2, sides))


if __name__ == "__main__":
    mcp.run()  # stdio transport
