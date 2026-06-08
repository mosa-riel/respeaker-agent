# Project plan — reSpeaker Agent

## Goal

A single, self-owned voice agent for the **reSpeaker XVF3800 + XIAO ESP32S3** that
replaces the fragile nested Home-Assistant integration chain. It:

1. **Owns the device audio** — talks to the ESP32 directly over the ESPHome native
   API (on-device wake word stays).
2. Runs an **LLM tool-calling loop** whose tools come from **multiple MCP servers**
   (Home Assistant first; more later).
3. Renders **custom screens to the reTerminal E1001 e-paper** depending on the
   request (also an ESPHome device).
4. Uses **OpenAI-compatible** STT/LLM/TTS endpoints (Mistral now: Voxtral + chat;
   Piper for TTS) — endpoint base_url is swappable to a future self-hosted model.
5. Exposes a **local web UI** for config + a **live trace monitor** of everything
   flowing through the pipeline.

## Architecture

```
agent service (Python, asyncio)
├─ device layer  (aioesphomeapi)
│   ├─ reSpeaker: wake event → mic audio in → TTS audio out
│   └─ reTerminal: push rendered screen (e-paper)   [phase 3]
├─ voice loop    wake → STT → agent → TTS → back to reSpeaker        [phase 2]
├─ agent loop    OpenAI-compatible /chat/completions + tools
│                 aggregated from N MCP servers (+ a built-in show_screen tool)
├─ trace bus     every stage emits structured events (trace.py)
└─ FastAPI       local UI: status, config, live monitor (SSE)
```

### Key design decisions
- **Python**, not Go: `aioesphomeapi` implements the native voice flow and drives
  both ESPHome devices; Pillow renders e-paper. Go would mean hand-writing the voice
  state machine + rendering.
- **OpenAI-compatible `/chat/completions` + `tools`**, NOT Mistral's proprietary
  Agents API — stays provider-agnostic (Mistral → self-hosted later). `tool_choice`
  `"any"` forces a call on must-act turns.
- **Verify, don't trust** the model: assert the tool actually ran / state changed
  before reporting success. (Small models hallucinate "done" — that's why HA's
  fallback needed `mistral-medium`.)
- Tool schema = OpenAI/MCP shape: `{"type":"function","function":{name,description,parameters}}`.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Device link + web UI + live trace monitor | ✅ done (step 01) |
| 2 | Voice flow: wake → mic audio → STT(Voxtral) → LLM(+MCP tools) → TTS(Voxtral TTS) | ✅ working on hardware (server-VAD, FLAC-URL TTS, follow-up + chime, flow-graph UI, multi-MCP) — see steps 02–08 |
| 3 | `show_screen` → render (Pillow) + push to reTerminal e-paper | 🔶 reTerminal flashed (ESPHome) + push proven; screen-MCP started (see below) |
| 4 | MCP client layer + multi-server config UI; tool dispatch | (overlaps p2) |
| 5 | Package as HA add-on (Docker); deploy on the HA host | |

### Phase 2 notes
- **TTS = Mistral Voxtral TTS** (`voxtral-mini-tts-2603`), not Piper — same
  provider as STT+chat, Dutch, ~0.8s TTFB on `pcm`, voice cloning, open weights.
  Pluggable behind `tts.py` so a local server is a config swap. See
  [reference/voice-audio.md](reference/voice-audio.md). (Done: step 02.)
- The voice flow over the native API uses `VoiceAssistant*` messages incl.
  `VoiceAssistantAudio` (audio over the TCP API, no separate UDP). Confirmed in
  the installed `aioesphomeapi`: `subscribe_voice_assistant(handle_start,
  handle_stop, handle_audio)` with the `API_AUDIO` subscription flag; events via
  `send_voice_assistant_event(...)`, TTS via `send_voice_assistant_audio(bytes)`.
- **Conflict:** only one client can be the device's voice handler. While HA still
  has the device adopted, HA owns voice. To let the agent own it, stop HA from
  handling this device's pipeline (or remove it from HA).
- Emit at every step: `wake`, `stt` (transcript), `llm-req` (full prompt+tools),
  `llm-rsp`, `tool` (name+args+result), `tts` (text) → all visible in the monitor.

### Phase 3 notes — reTerminal e-paper via a screen-MCP
- reTerminal E1001 = ESP32-S3 + 7.5" mono e-paper 800x480, deep-sleeps, ships with
  SenseCraft (cloud, no local API) → **flashed with ESPHome** for local control
  (`../ReSpeaker/devices/reterminal-e1001/`). Pinout from Seeed's ESPHome cookbook.
- **Push pattern (proven on hardware):** the device has `online_image`
  (`update_interval: never`) + an ESPHome API service `refresh_screen`. A server
  hosts a rendered PNG; calling `refresh_screen` (via `aioesphomeapi.execute_service`)
  makes the device fetch + redraw. Agent-initiated, near-instant (e-paper refresh
  ~2-5s, no video).
- **Architecture:** a dedicated **screen-MCP** (`scripts/screen_mcp.py`) owns this —
  tools `show_text`/`show_image`/`clear_screen` render the PNG, host it (:8790), and
  trigger the device. The agent just calls the tool. Becomes a standalone HTTP MCP
  pod under the containerization plan. (Wiring TODO: point the device's online_image
  url at the screen-MCP + register it in config.)

## Security gates (enforced)

- No binding off `127.0.0.1` without auth + CSRF + same-origin.
- No launching MCP subprocesses from UI-supplied `command`/`args` — server-side
  allowlist only. See [reference/security.md](reference/security.md).
