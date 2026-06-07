"""FastAPI app: serves the local UI + status/config API.

Secrets are never returned by or accepted through these endpoints — only the
non-secret Settings are editable here. Bind to 127.0.0.1 by default.
"""

from __future__ import annotations

import asyncio
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
from .agent import AgentLoop, ConversationStore
from .config import McpServer, Secrets, Settings
from .device import DeviceLink
from .home_context import HomeContext
from .mcp_client import McpManager
from .stt import STTClient
from .tools import demo_registry
from .trace import TraceBus
from .tts import make_tts
from .voice import VoicePipeline

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
    app.state.convos = ConversationStore()
    app.state.stt = STTClient(settings, secrets, trace)
    app.state.tts = make_tts(settings, secrets, trace)
    mcp = McpManager(settings, trace, tools)
    app.state.mcp = mcp
    home_ctx = HomeContext(mcp, trace)
    app.state.home_ctx = home_ctx
    voice = VoicePipeline(settings, trace, app.state.stt, app.state.agent, app.state.tts, app.state.convos, home_ctx)
    app.state.voice = voice
    if settings.voice_enabled:
        link.post_connect = voice.attach  # (re)subscribe as voice handler on connect
        trace.emit("info", "voice pipeline ENABLED (will own the device's voice)")
    trace.emit("info", "agent starting")
    await mcp.start()  # connect MCP servers + populate the tool registry FIRST
    await home_ctx.refresh()  # ground truth for the prompt (best-effort)
    await link.start()  # connect device last → post_connect can attach voice
    refresh_task = asyncio.create_task(_periodic_home_refresh(home_ctx, settings))
    try:
        yield
    finally:
        refresh_task.cancel()
        voice.detach()
        await mcp.stop()
        await link.stop()


async def _periodic_home_refresh(ctx: HomeContext, settings: Settings) -> None:
    try:
        while True:
            await asyncio.sleep(max(60, settings.home_context_refresh_sec))
            await ctx.refresh()
    except asyncio.CancelledError:
        pass


app = FastAPI(title="reSpeaker Agent", lifespan=lifespan)


@app.get("/api/status")
async def status() -> JSONResponse:
    data = app.state.link.status()
    data["voice"] = app.state.voice.status()
    return JSONResponse(data)


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
        q = bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Poll with a timeout so we re-check disconnect and stay
                    # cancellable. Cancelling queue.get() on timeout leaves the
                    # subscription intact (no generator teardown).
                    evt = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            bus.unsubscribe(q)

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
    for key in {"voice_enabled"} & payload.keys():
        setattr(settings, key, bool(payload[key]))
    settings.save()
    return JSONResponse({"ok": True, "note": "Restart the agent to apply device changes."})


@app.get("/api/home")
async def get_home_context() -> JSONResponse:
    ctx: HomeContext = app.state.home_ctx
    return JSONResponse({"entity_count": ctx.entity_count, "context": ctx.get() or ""})


@app.post("/api/home/refresh")
async def refresh_home_context() -> JSONResponse:
    # Manual "vernieuw apparaten" — also runs periodically + at startup.
    ctx: HomeContext = app.state.home_ctx
    count = await ctx.refresh()
    return JSONResponse({"ok": True, "entity_count": count, "context": ctx.get() or ""})


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
    convos: ConversationStore = app.state.convos
    conv_id = str(payload.get("conversation_id") or "default")
    if payload.get("reset"):
        convos.clear(conv_id)
    try:
        result = await app.state.agent.run(
            text,
            history=convos.get(conv_id),
            context=app.state.home_ctx.get(),
            force_tool=bool(payload.get("force_tool")),
        )
    except Exception as err:  # noqa: BLE001 - surface to UI, already traced
        return JSONResponse({"ok": False, "error": _safe(err)}, status_code=502)
    convos.update(conv_id, result.messages)  # persist the turn for follow-ups
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


@app.get("/api/mcp")
async def list_mcp() -> JSONResponse:
    settings: Settings = app.state.settings
    status = app.state.mcp.status
    servers = []
    for srv in settings.mcp_servers:
        st = status.get(srv.name, {})
        servers.append({
            "name": srv.name,
            "transport": srv.transport,
            "target": srv.url or (f"{srv.command} {' '.join(srv.args)}".strip()),
            "enabled": srv.enabled,
            "connected": bool(st.get("connected")),
            "tool_count": len(st.get("tools", [])),
            "error": st.get("error"),
            # Full tool catalog (name/description/enabled) for the UI toggle list —
            # only known after a successful connect.
            "all_tools": st.get("all_tools", []),
            "enabled_tools": srv.enabled_tools,
        })
    return JSONResponse({"servers": servers})


@app.post("/api/mcp")
async def add_mcp(payload: dict) -> JSONResponse:
    # SECURITY: only REMOTE (http/sse url) servers can be added via the API — they
    # launch no subprocess. stdio servers (command/args) run code, so they must be
    # configured in config.json by hand, never from this unauthenticated endpoint.
    # See docs/reference/security.md.
    if payload.get("command") or payload.get("args"):
        return JSONResponse(
            {"ok": False, "error": "stdio (command/args) servers must be added in config.json, not via the API"},
            status_code=403,
        )
    name = (payload.get("name") or "").strip()
    url = (payload.get("url") or "").strip()
    if not name or not url:
        return JSONResponse({"ok": False, "error": "name and url are required"}, status_code=422)
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"ok": False, "error": "url must be http(s)"}, status_code=422)
    settings: Settings = app.state.settings
    if any(m.name == name for m in settings.mcp_servers):
        return JSONResponse({"ok": False, "error": f"server '{name}' already exists"}, status_code=409)
    settings.mcp_servers.append(McpServer(name=name, url=url, enabled=True))
    settings.save()
    return JSONResponse({"ok": True, "note": "Added. Restart the agent to connect."})


@app.patch("/api/mcp/{name}")
async def toggle_mcp(name: str, payload: dict) -> JSONResponse:
    # Enable/disable the whole server, and/or set its per-tool disabled list.
    # Tool curation only — NOT a security boundary (that's the server's own creds).
    settings: Settings = app.state.settings
    found = False
    for m in settings.mcp_servers:
        if m.name != name:
            continue
        if "enabled" in payload:
            m.enabled = bool(payload["enabled"])
        if "enabled_tools" in payload:
            et = payload["enabled_tools"]
            if not isinstance(et, list) or not all(isinstance(x, str) for x in et):
                return JSONResponse({"ok": False, "error": "enabled_tools must be a list of strings"}, status_code=422)
            m.enabled_tools = et
        found = True
    if not found:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    settings.save()
    return JSONResponse({"ok": True, "note": "Saved. Restart the agent to apply."})


@app.delete("/api/mcp/{name}")
async def remove_mcp(name: str) -> JSONResponse:
    settings: Settings = app.state.settings
    before = len(settings.mcp_servers)
    settings.mcp_servers = [m for m in settings.mcp_servers if m.name != name]
    if len(settings.mcp_servers) == before:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    settings.save()
    return JSONResponse({"ok": True, "note": "Removed. Restart the agent to apply."})


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
