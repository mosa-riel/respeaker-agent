# Step 08 — Voice UX: on-device bring-up, follow-up, flow-graph, multi-MCP

**Date:** 2026-06-07
**Goal:** Get the phase-2 voice loop actually working on the real reSpeaker and make
it pleasant. Supersedes the planning notes in step 07.

## How the device actually works (formatBCE XVF3800 firmware)

- **Wake** = on-device `micro_wake_word` ("okay nabu"). It only acts if the mic isn't
  muted; the firmware auto-unmutes on `on_client_connected` when `init_in_progress`
  (fresh boot) — so connect the agent, then reboot the device once.
- **No on-device VAD / `use_wake_word:false`** → the device streams mic audio
  continuously and expects the **server** to detect end-of-speech. We run an energy
  VAD in `voice.py` (`vad_threshold` / `vad_silence_ms` / `vad_max_ms` /
  `vad_prespeech_ms`) and send `STT_VAD_START/END`.
- **TTS playback is URL-based**: the device's `media_player` fetches a URL and decodes
  it — it **rejects WAV** ("could not determine audio file type"); its pipeline is
  **FLAC**. So we serve the reply as FLAC on a small LAN audio server (`tts_server.py`,
  audio-only) and hand the device the URL. STT stays 16 kHz; TTS = Voxtral FLAC.
- **Event order matters for the LEDs**: `RUN_START → STT_START` (at wake, lights the
  listening beam) → `STT_VAD_START/END` → `STT_END{text}` → `INTENT_*` → `TTS_START`
  → playback → `RUN_END`.
- **Only one voice client** — Home Assistant must not have the device adopted. We
  connect directly over the native API (`voice_enabled`).

## Follow-up conversation (default on)

`voice_followup=True`: the reply is delivered with
`send_voice_assistant_announcement_await_response(url, start_conversation=True)`. That
**awaits real playback end** (no more tail-clipping / FLAC-duration guessing) and
**re-opens the mic** without a new wake word. The session loops while you keep
talking; it ends when you're silent for ~`vad_prespeech_ms`. On that end we play a
short **FLAC chime** (`make_chime_flac`, `voice_end_chime`) so you know the mic closed.
`RUN_END` is guarded (`_run_end`, once per turn) since the announce path ends the run
early. The wake-each-time path (`TTS_END{url}` + tail-sleep) remains when follow-up is
off.

## Grounding, model, latency

- STT forced to Dutch (`stt_language=nl`) — Voxtral honours `language` (verified).
- `mistral-small` + curated tools + `llm_temperature=0.1` + the grounded entity list
  → control commands ~1–2 s and no fabrication. LLM calls retried on 429/5xx.

## Multi-MCP

`home-assistant` (curated to device-control tools) + **`funbox`** (a local FastMCP
demo: jokes / 8-ball / coin / dice, `scripts/funbox_mcp.py`) prove N-server fan-in.
Add remote (url) servers from the UI; stdio servers live in `config.json`.

## UI — living flow-graph

The pipeline is an organic flow-graph dashboard: Wake → STT → LLM → Tools → TTS with
curved SVG connectors and a comet pulse that flows **in the real event direction**
(incl. the LLM↔Tools back-and-forth per tool call), bubbles that persist, a TTS
equaliser synced to clip length, and a detached Device chip. A follow-up toggle
(live, no restart) + Refresh-devices button live in the header / MCP card.

## Open

- `call_service` domain allowlist (admin token can call anything).
- Optional: clickable pipeline nodes → per-stage settings; sidebar restructure.
</content>
</invoke>
