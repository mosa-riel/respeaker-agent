# Project plan — reSpeaker Agent

## Goal

A single, self-hosted voice agent for the **reSpeaker XVF3800 + XIAO ESP32S3** that replaces
a fragile nested chain of Home Assistant integrations. It:

1. **Owns the device audio**: talks to the ESP32 directly over the ESPHome native API; the
   wake word stays on the device.
2. Runs an **LLM tool-calling loop** whose tools come from **multiple MCP servers** (Home
   Assistant, screen, search, …).
3. Shows **cards on a reTerminal E1001 e-paper** screen next to the spoken answer.
4. Uses **OpenAI-compatible** STT/LLM/TTS endpoints (Mistral by default), so a self-hosted
   model is a base-URL change.
5. Exposes a **web UI** for configuration and a **live trace monitor** of the pipeline.

## Architecture

```
agent (Python, asyncio) — HA add-on, bridged network, UI via ingress
├─ device layer  aioesphomeapi → reSpeaker: wake, mic audio, announcements
├─ voice loop    wake → VAD → STT → agent → TTS → device speaker or host sink
├─ agent loop    /chat/completions + tools from N MCP servers (+ opt-in Bluetooth tools)
├─ home context  room-grouped device list from HA, injected into the prompt
├─ trace bus     every stage emits structured events; turn log in /data/turns.jsonl
├─ TTS audio     LAN HTTP server (:8731) the device fetches replies from
└─ FastAPI       paged UI: pipeline, live trace, playground, Bluetooth, settings, MCP

MCP add-ons (one repo each, streamable HTTP): homeassistant, screen, websearch, funbox, …
```

## Key design decisions

- **Python**: `aioesphomeapi` implements the native voice flow; Go would mean hand-writing
  that state machine.
- **OpenAI-compatible `/chat/completions` + `tools`**, not Mistral's Agents API, to stay
  provider-agnostic. `tool_choice: "any"` can force a call on must-act turns.
- **Verify, don't trust**: report what the tool actually returned, not the model's "done".
- **Tools come from MCP servers reached by URL**, one add-on per server, so adding a source
  is configuration only.
- **Ground the model** in a list of the home's real devices with entity ids, so it acts
  directly and does not invent devices (steps 06, 12).

## Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Device link, web UI, live trace monitor | done (step 01) |
| 2 | Voice: wake → mic → STT → LLM + MCP tools → TTS | done (steps 02–08, tuned in 12–14) |
| 3 | e-paper screen via `mcp-screen` | done: reTerminal on ESPHome, cards next to answers (step 14) |
| 4 | MCP client layer, multi-server config UI, tool dispatch | done (steps 04, 09) |
| 5 | Home Assistant add-ons on the HA host | done (steps 09–11; [deployment](reference/deployment.md)) |

### Voice over the native API
- Audio travels over the TCP API (`subscribe_voice_assistant` with `API_AUDIO`), not UDP.
- **Only one client can be the device's voice handler.** Stop Home Assistant's Assist
  pipeline for the device before the agent takes over (`voice_enabled`).
- Every stage emits to the trace (`wake`, `stt`, `llm-req`, `llm-rsp`, `tool`, `tts`).

### e-paper screen
- The reTerminal E1001 (ESP32-S3, 7.5" 800×480 mono e-paper) ships with SenseCraft (cloud, no
  local API); it is flashed with ESPHome for local control (`devices/reterminal-e1001/`).
- Push pattern: the device has `online_image` (`update_interval: never`) and an API service
  `refresh_screen`. `mcp-screen` renders a PNG, hosts it on `:8799`, and calls the service;
  the device fetches and redraws (2–5 s).

## Security gates

- No binding off `127.0.0.1` without auth + CSRF + same-origin (ingress satisfies this in the
  add-on).
- Never launch MCP subprocesses from UI-supplied `command`/`args`; server-side allowlist only.

See [reference/security.md](reference/security.md).
