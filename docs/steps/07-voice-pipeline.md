# Step 07 — Voice pipeline over the ESPHome native API

**Date:** 2026-06-07
**Goal:** talk to the reSpeaker end to end: wake → mic → STT → agent → TTS → speaker. Step 08
records what changed once it ran on the hardware.

## How it works — `voice.py` (`VoicePipeline`)

Based on the `aioesphomeapi` source:
- `cli.subscribe_voice_assistant(handle_start, handle_stop, handle_audio)`: passing
  `handle_audio` sets the `API_AUDIO` flag, so mic audio comes over the TCP API (no UDP);
  `handle_start` returns `0`.
- On wake: emit `wake`, send `RUN_START`; the device streams 16 kHz/16-bit/mono PCM to
  `handle_audio`. The turn then runs `STT_START` → STT → `STT_END{text}` → `INTENT_START` →
  `AgentLoop.run(history, context)` → `INTENT_END` → `TTS_START{text}` → audio → `TTS_END` →
  `RUN_END`. Errors emit `VOICE_ASSISTANT_ERROR` and still send `RUN_END`, so the device
  never hangs.
- History per conversation and the home context: the same agent path as the test chat.
- `DeviceLink.post_connect(cli)` re-subscribes on every reconnect; `VoicePipeline.attach()`
  unsubscribes a previous handler first. Built after MCP and the home context in the lifespan.

## Opt-in: `voice_enabled`

Only one client can be the device's voice handler, and Home Assistant owns it while its
Assist pipeline handles the device. To switch:

1. Stop HA's Assist pipeline for the device (unassign the pipeline or remove the device from
   HA).
2. Set `voice_enabled: true` and restart.
3. Say the wake word ("Okay Nabu") and watch the live monitor:
   `wake → stt → llm-req/rsp → tool → tts`.
