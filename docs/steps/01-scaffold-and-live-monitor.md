# Step 01 — Scaffold & live monitor

**Date:** 2026-06-07
**Goal:** connect to the reSpeaker over the ESPHome native API, serve a local web UI for
status and config, and add a live pipeline trace monitor for later phases to emit into.

## Layout

```
src/respeaker_agent/
  config.py     # Settings (config.json, editable) vs Secrets (env-only)
  device.py     # DeviceLink: resilient aioesphomeapi connection (ReconnectLogic)
  trace.py      # TraceBus: structured pipeline events + pub/sub
  web.py        # FastAPI: /api/status, /api/trace (+ /stream SSE), /api/config
  cli.py        # entrypoint → uvicorn
  static/{index.html,app.css}   # no-build UI
pyproject.toml · uv.lock · config.example.json · .env.example · sbom.json
```

## Run

```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
uv run respeaker-agent          # http://127.0.0.1:8730
```

## Observability

`trace.py` is the single event stream: every stage calls
`bus.emit(stage, text, direction=, data=, level=)`. Stages: `device, wake, stt, llm-req,
llm-rsp, tool, tts, info, error`. The UI subscribes to `/api/trace/stream` (SSE) and shows
stage-coloured rows with expandable JSON payloads, per-stage filters, pause and clear.

## Verified

- Connects to the device over the native API; live state changes reach the monitor.
- Config validation: a bad `device_port` → 422; `web_host`/`web_port`/`mcp_servers` are not
  writable via the API (see [security](../reference/security.md)).
