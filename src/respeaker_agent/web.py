"""FastAPI app: serves the local UI + status/config API.

Secrets are never returned by or accepted through these endpoints — only the
non-secret Settings are editable here. Bind to 127.0.0.1 by default.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Secrets, Settings
from .device import DeviceLink
from .trace import TraceBus

STATIC_DIR = Path(__file__).parent / "static"
SBOM_PATH = Path(__file__).resolve().parents[2] / "sbom.json"  # repo root


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load()
    secrets = Secrets.from_env()
    trace = TraceBus()
    link = DeviceLink(settings, secrets, trace)
    app.state.settings = settings
    app.state.trace = trace
    app.state.link = link
    trace.emit("info", "agent starting")
    await link.start()
    try:
        yield
    finally:
        await link.stop()


app = FastAPI(title="reSpeaker Agent", lifespan=lifespan)


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse(app.state.link.status())


@app.get("/api/trace")
async def trace_snapshot(limit: int = 200) -> JSONResponse:
    return JSONResponse(app.state.trace.recent(min(limit, 500)))


@app.get("/api/sbom")
async def sbom() -> JSONResponse:
    # Serves the committed SBOM. Regenerate with `make sbom`.
    if not SBOM_PATH.exists():
        return JSONResponse({"error": "sbom.json not found — run `make sbom`"}, status_code=404)
    return FileResponse(SBOM_PATH, media_type="application/json", filename="sbom.json")


@app.get("/api/trace/stream")
async def trace_stream(request: Request) -> StreamingResponse:
    bus: TraceBus = app.state.trace

    async def gen():
        # Replay recent events first so a fresh tab is in sync, then go live.
        for evt in reversed(bus.recent(50)):
            yield f"data: {json.dumps(evt)}\n\n"
        async for evt in bus.stream():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def get_config() -> JSONResponse:
    # Settings only — secrets are never exposed here.
    return JSONResponse(asdict(app.state.settings))


@app.put("/api/config")
async def put_config(payload: dict) -> JSONResponse:
    # SECURITY: only these fields are writable via the UI. Network-bind fields
    # (web_host/web_port) are intentionally NOT editable here — changing them
    # remotely could expose the agent on 0.0.0.0. mcp_servers is also excluded:
    # its command/args become subprocess exec, so it must never be set from UI
    # input (edit config.json directly until a server-side allowlist exists).
    # See docs/reference/agent-security.md before relaxing this.
    settings: Settings = app.state.settings
    str_fields = {"device_host", "llm_base_url", "llm_model"}
    for key in str_fields & payload.keys():
        val = payload[key]
        if not isinstance(val, str):
            return JSONResponse({"ok": False, "error": f"{key} must be a string"}, status_code=422)
        setattr(settings, key, val)
    if "device_port" in payload:
        try:
            port = int(payload["device_port"])
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "device_port must be an integer"}, status_code=422)
        if not (1 <= port <= 65535):
            return JSONResponse({"ok": False, "error": "device_port out of range"}, status_code=422)
        settings.device_port = port
    settings.save()
    return JSONResponse({"ok": True, "note": "Restart the agent to apply device changes."})


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
