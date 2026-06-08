"""Screen — an MCP server that drives the reTerminal E1001 e-paper.

Self-contained: a tool renders/accepts content, this server HOSTS the resulting PNG
on the LAN, and triggers the device's `refresh_screen` ESPHome service so it fetches
+ redraws. The agent just calls a tool (show_text / show_image / clear_screen).

The reTerminal firmware's online_image URL must point at this server:
  http://<this-host-ip>:<SCREEN_PORT>/screen.png   (default :8790)

Env: RETERMINAL_HOST (default reterminal-e1001.local), SCREEN_PORT (8790).
Run: uv run python scripts/screen_mcp.py   (stdio MCP)
"""

from __future__ import annotations

import asyncio
import glob
import io
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from aioesphomeapi import APIClient
from mcp.server.fastmcp import FastMCP
from PIL import Image, ImageDraw, ImageFont

RETERMINAL = os.getenv("RETERMINAL_HOST", "reterminal-e1001.local")
PORT = int(os.getenv("SCREEN_PORT", "8790"))
W, H = 800, 480

_png: bytes | None = None
_lock = threading.Lock()


def _font(size: int, bold: bool = False):
    for p in glob.glob(f"/usr/share/fonts/**/DejaVuSans{'-Bold' if bold else ''}.ttf", recursive=True):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out or [""]


def _render_text(title: str, body: str) -> bytes:
    img = Image.new("1", (W, H), 1)  # 1-bit, white
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, W - 7, H - 7], outline=0, width=3)
    d.text((40, 36), title[:42], font=_font(60, True), fill=0)
    y = 140
    for line in _wrap(body, 40)[:8]:
        d.text((40, y), line, font=_font(34), fill=0)
        y += 46
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _publish(data: bytes) -> None:
    global _png
    with _lock:
        _png = data


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        with _lock:
            data = _png
        if not self.path.rstrip("/").endswith("screen.png") or data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_server() -> None:
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", PORT), _Handler).serve_forever, daemon=True).start()


async def _refresh_device() -> str:
    """Trigger the reTerminal to re-fetch + redraw."""
    cli = APIClient(RETERMINAL, 6053, None)
    await cli.connect(login=False)
    try:
        _, services = await cli.list_entities_services()
        svc = next((s for s in services if s.name == "refresh_screen"), None)
        if svc is None:
            return "device heeft geen refresh_screen service"
        res = cli.execute_service(svc, {})
        if asyncio.iscoroutine(res):
            await res
        return "ok"
    finally:
        await cli.disconnect()


mcp = FastMCP("screen")


@mcp.tool()
async def show_text(title: str, body: str = "") -> str:
    """Toon tekst op het e-paper scherm (reTerminal). title = grote kop, body = tekst
    eronder. Gebruik dit om iets visueel te tonen aan de gebruiker."""
    _publish(_render_text(title, body))
    return f"Scherm bijgewerkt ({await _refresh_device()}): {title}"


@mcp.tool()
async def show_image(url: str) -> str:
    """Toon een afbeelding van een URL op het scherm (wordt naar 800x480 1-bit
    geschaald)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("L").resize((W, H)).convert("1")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    _publish(buf.getvalue())
    return f"Afbeelding getoond ({await _refresh_device()})"


@mcp.tool()
async def clear_screen() -> str:
    """Maak het scherm leeg (wit)."""
    buf = io.BytesIO()
    Image.new("1", (W, H), 1).save(buf, "PNG")
    _publish(buf.getvalue())
    return f"Scherm leeg ({await _refresh_device()})"


if __name__ == "__main__":
    _start_server()
    mcp.run()  # stdio
