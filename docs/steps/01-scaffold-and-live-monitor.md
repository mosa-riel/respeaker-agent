# Step 01 — Scaffold & live monitor

**Date:** 2026-06-07
**Goal:** Stand up the agent: connect to the reSpeaker over the ESPHome native API,
a local web UI for status/config, and a live pipeline trace monitor (the foundation
phases 2–3 emit into).

## Layout

```
src/respeaker_agent/
  config.py     # Settings(json, editable) vs Secrets(env-only)
  device.py     # DeviceLink: resilient aioesphomeapi connection (ReconnectLogic)
  trace.py      # TraceBus: structured pipeline events + pub/sub
  web.py        # FastAPI: /api/status, /api/trace(+/stream SSE), /api/config
  cli.py        # entrypoint → uvicorn
  static/{index.html,app.css}   # no-build UI (styling lifted from mosa commander)
pyproject.toml · uv.lock · config.example.json · .env.example · sbom.json
```

## Run

```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
uv run respeaker-agent          # http://127.0.0.1:8730
```

## Verified (against the real device)

- Connects to `respeaker-xvf3800-assistant` (ESPHome 2026.5.3, 17 entities) via the
  native API over the network.
- `/api/status`, `/api/trace` (snapshot), `/api/trace/stream` (SSE) all serve; live
  device state changes flow into the monitor.
- Config validation: bad `device_port` → 422; `web_host`/`web_port`/`mcp_servers`
  are **not** writable via the API (security — see reference/security.md).

## Observability

`trace.py` is the single event stream. Every stage calls
`bus.emit(stage, text, direction=, data=, level=)`. Stages: `device, wake, stt,
llm-req, llm-rsp, tool, tts, info, error`. The UI opens an `EventSource` on
`/api/trace/stream` and renders stage-coloured rows with expandable JSON payloads
(full prompts, transcripts, tool args, responses), per-stage filters, pause, clear.

## Next

Phase 2 — voice flow (wake → mic audio → STT → LLM + MCP tools → TTS), all emitting
into the trace bus. See ../PLAN.md.
