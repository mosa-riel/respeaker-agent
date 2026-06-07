"""FastAPI app: serves the local UI + status/config API.

Secrets are never returned by or accepted through these endpoints — only the
non-secret Settings are editable here. Bind to 127.0.0.1 by default.
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import audio
from .agent import AgentLoop
from .config import Secrets, Settings
from .device import DeviceLink
from .stt import STTClient
from .tools import demo_registry
from .trace import TraceBus
from .tts import make_tts

STATIC_DIR = Path(__file__).parent / "static"
SBOM_PATH = Path(__file__).resolve().parents[2] / "sbom.json"  # repo root


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load()
    secrets = Secrets.from_env()
    trace = TraceBus()
    link = DeviceLink(settings, secrets, trace)
    tools = demo_registry()
    app.state.settings = settings
    app.state.secrets = secrets
    app.state.trace = trace
    app.state.link = link
    app.state.tools = tools
    app.state.agent = AgentLoop(settings, secrets, trace, tools)
    app.state.stt = STTClient(settings, secrets, trace)
    app.state.tts = make_tts(settings, secrets, trace)
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
    # Non-secret string fields. STT/TTS/LLM endpoints are safe to edit (they're just
    # URLs/model ids/voice id — not subprocess exec like mcp_servers, not a network
    # bind like web_host). tts_provider is validated against a fixed set below.
    str_fields = {
        "device_host", "llm_base_url", "llm_model", "system_prompt",
        "stt_base_url", "stt_model",
        "tts_provider", "tts_base_url", "tts_model", "tts_voice_id", "tts_format",
    }
    for key in str_fields & payload.keys():
        val = payload[key]
        if not isinstance(val, str):
            return JSONResponse({"ok": False, "error": f"{key} must be a string"}, status_code=422)
        if key == "tts_provider" and val.lower() not in ("voxtral", "openai", "openai-compatible"):
            return JSONResponse({"ok": False, "error": "tts_provider must be voxtral|openai"}, status_code=422)
        setattr(settings, key, val)
    int_fields = {
        "device_port": (1, 65535),
        "tts_pcm_rate": (8000, 48000),
        "tts_out_rate": (8000, 48000),
        "max_tool_rounds": (1, 20),
    }
    for key, (lo, hi) in int_fields.items():
        if key not in payload:
            continue
        try:
            num = int(payload[key])
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": f"{key} must be an integer"}, status_code=422)
        if not (lo <= num <= hi):
            return JSONResponse({"ok": False, "error": f"{key} out of range ({lo}–{hi})"}, status_code=422)
        setattr(settings, key, num)
    settings.save()
    return JSONResponse({"ok": True, "note": "Restart the agent to apply device changes."})


@app.get("/api/tools")
async def list_tools() -> JSONResponse:
    # Tool names/specs available to the agent (demo set now; MCP-fed in phase 4).
    return JSONResponse(app.state.tools.specs())


@app.post("/api/run")
async def run_agent(payload: dict) -> JSONResponse:
    """Manual prompt → full agent loop (llm → tools → … → reply). Optional `speak`
    synthesizes the reply and returns it as base64 WAV for in-browser playback.
    Lets the whole pipeline be tested without the device owning voice."""
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "text is required"}, status_code=422)
    settings: Settings = app.state.settings
    try:
        result = await app.state.agent.run(text, force_tool=bool(payload.get("force_tool")))
    except Exception as err:  # noqa: BLE001 - surface to UI, already traced
        return JSONResponse({"ok": False, "error": _safe(err)}, status_code=502)
    out: dict = {"ok": True, "reply": result.text, "rounds": result.rounds, "tool_calls": result.tool_calls}
    if payload.get("speak") and result.text:
        try:
            pcm = await app.state.tts.synth(result.text)
            wav = audio.int16_to_wav_bytes(np.frombuffer(pcm, dtype="<i2"), settings.tts_out_rate)
            out["audio_wav_b64"] = base64.b64encode(wav).decode()
        except Exception as err:  # noqa: BLE001
            out["tts_error"] = _safe(err)
    return JSONResponse(out)


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
