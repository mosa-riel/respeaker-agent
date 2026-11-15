# Step 08 — Voice UX: on-device bring-up, follow-up, flow-graph, multi-MCP

**Date:** 2026-06-07
**Goal:** make the voice loop work on the real reSpeaker and pleasant to use. Supersedes the
assumptions in step 07.

## How the device actually behaves (formatBCE XVF3800 firmware)

- **Wake** is on-device `micro_wake_word` ("okay nabu"). It only acts when the mic is not
  muted; the firmware unmutes on `on_client_connected` during a fresh boot, so connect the
  agent and then reboot the device once.
- **No end-of-speech from the device:** it streams the mic continuously and expects the
  server to detect the end. The agent runs its own VAD (`vad_threshold`, `vad_silence_ms`,
  `vad_max_ms`, `vad_prespeech_ms`) and sends `STT_VAD_START/END`.
- **TTS playback is by URL:** the `media_player` fetches and decodes a URL and **rejects
  WAV**; its pipeline is FLAC. The agent serves the reply as FLAC on a small LAN audio server
  (`tts_server.py`) and hands the device the URL.
- **Event order drives the LEDs:** `RUN_START → STT_START` (at wake, listening beam) →
  `STT_VAD_START/END` → `STT_END{text}` → `INTENT_*` → `TTS_START` → playback → `RUN_END`.

## Follow-up conversation

`voice_followup` (default on) delivers the reply with
`send_voice_assistant_announcement_await_response(url, start_conversation=True)`. That waits
for the real end of playback (no clipped tail, no FLAC-duration guessing) and re-opens the mic
without a new wake word. When nobody speaks, a short FLAC end chime (`voice_end_chime`)
signals that the mic closed. `RUN_END` is sent once per turn (`_run_end`). Step 14 made the
model decide per reply whether to keep listening.

## Grounding, model, latency

- STT forced to Dutch (`stt_language=nl`); Voxtral honours `language`.
- mistral-small + curated tools + `llm_temperature=0.1` + the grounded entity list: control
  commands in ~1–2 s without fabrication. LLM calls are retried on 429/5xx.

## Multi-MCP

`home-assistant` (curated to control tools) plus `funbox` (a small demo server: jokes,
8-ball, coin, dice) proved N-server fan-in.

## UI — flow-graph

The pipeline is shown as a flow-graph (Wake → STT → LLM → Tools → TTS) with curved
connectors and a pulse that follows the real event direction, including the LLM ↔ Tools
back-and-forth, plus a TTS equaliser synced to the clip length and a device chip. A live
follow-up toggle and a refresh-devices button sit in the header and MCP card.
