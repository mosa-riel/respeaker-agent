# Step 07 — Voice pipeline over the ESPHome native API

**Date:** 2026-06-07
**Goal:** Talk to the reSpeaker end-to-end: wake → mic → STT → agent → TTS → speaker.

## How it works — `voice.py` (`VoicePipeline`)

Grounded in the `aioesphomeapi` source (not guessed):
- `cli.subscribe_voice_assistant(handle_start, handle_stop, handle_audio)` — passing
  `handle_audio` auto-sets the `API_AUDIO` flag, so mic audio comes over the TCP API
  (no UDP). `handle_start` returns `0` (API-audio port).
- Flow: device wake → `handle_start` (emit `wake`, send `RUN_START`, return 0) →
  device streams 16 kHz/16-bit/mono PCM to `handle_audio` (buffered) → device sends
  `audio.end` which the library turns into **`handle_stop(False)` = end of speech**.
- On `handle_stop(False)` we run the turn: `STT_START` → Voxtral STT → `STT_END{text}`
  → `INTENT_START` → `AgentLoop.run(history, context)` → `INTENT_END` → `TTS_START{text}`
  → stream `send_voice_assistant_audio(chunk)` for each TTS chunk → `TTS_END` →
  `RUN_END`. Errors emit `VOICE_ASSISTANT_ERROR` and still send `RUN_END` so the
  device never hangs.
- Per-conversation history keyed by the device's `conversation_id`; home context
  injected — same agent path as the test chat, so behaviour matches.

Wiring: `DeviceLink.post_connect(cli)` hook re-subscribes on every (re)connect;
`VoicePipeline.attach()` unsubscribes any previous handler first. Built in the
lifespan after MCP + home context so all deps exist before the device connects.

## OPT-IN — `voice_enabled` (default OFF)

Only ONE client may be the device's voice handler, and **Home Assistant owns it**
while the device is adopted there. So this is off by default and they'd fight.

To enable + test on hardware:
1. Stop HA's pipeline for this device (unassign the Assist pipeline / remove the
   device from HA, per `../ReSpeaker` docs).
2. Set `"voice_enabled": true` in `config.json` (or PUT `/api/config`), restart.
3. Say the wake word ("Okay Nabu") and speak; watch the live monitor:
   `wake → stt → llm-req/rsp → tool → tts`.

## Not yet hardware-verified

Built against the API contract; needs an on-device pass to confirm:
- TTS playback sample rate: we send 16 kHz mono PCM (`tts_out_rate`). If pitch/speed
  is off on the speaker, adjust `tts_out_rate` to what the firmware expects.
- Whether the firmware wants a specific `send_voice_assistant_audio` chunk size /
  an explicit audio-end marker beyond `TTS_END`.
- Total latency budget: STT + agent (~1–2s) + TTS first-audio (~0.8s). Sentence-
  chunking the TTS (first sentence while the rest synthesises) is the next tuning if
  needed.

## Open / next

- On-device verification (above).
- Voice "vernieuw apparaten" → `home_ctx.refresh()` as a recognised intent.
- OPEN security: `call_service` domain allowlist (admin token can call anything).
- UI: sidebar (#6) + clickable pipeline visualization (#7).
</content>
</invoke>
