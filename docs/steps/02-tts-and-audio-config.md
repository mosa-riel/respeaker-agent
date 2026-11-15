# Step 02 — Pluggable TTS + audio layer + config surface

**Date:** 2026-06-07
**Goal:** a swappable text-to-speech path (hosted Voxtral or a local server), the audio
conversions the device needs, and config for the STT/TTS/LLM endpoints.

## Decisions

- **TTS = Mistral Voxtral TTS** (`voxtral-mini-tts-2603`), not Piper: same provider as STT and
  chat (one key), Dutch support, ~0.8 s to first audio on `pcm`, zero-shot voice cloning, and
  open weights (CC BY-NC 4.0), so self-hostable later. Piper is fast enough but its Dutch
  voices are weak; cloning solves that.
- **Speed over quality:** request `pcm` (float32 LE, 24 kHz) with `stream: true`, decode and
  resample to 16 kHz as chunks arrive, soxr `LQ`. `wav` (~3 s to first audio) is the fallback.
- **Engine swappable by config alone:** `tts_base_url` (+ `tts_provider`), no code change.

## What was built

- `audio.py`: numpy/soxr conversions (`audioop` is gone in Python 3.13+):
  `float32le_to_int16`, `resample_int16`, `wav_to_int16_mono`, `int16_to_wav_bytes`.
- `tts.py`: `TTSClient.synth_stream(text)` with adapters `VoxtralTTS` and `OpenAITTS`;
  `make_tts()` picks by `tts_provider`. See [voice-audio](../reference/voice-audio.md).
- `config.py`: `stt_base_url/stt_model`, `tts_provider/tts_base_url/tts_model/tts_voice_id/
  tts_format/tts_pcm_rate/tts_out_rate`; secrets `STT_API_KEY`/`TTS_API_KEY` fall back to
  `LLM_API_KEY`.
- `web.py`: the `PUT /api/config` allowlist gained these fields with type and range checks;
  `mcp_servers` and `web_host`/`web_port` stay excluded.

## Verified

Against the real Voxtral API with a custom voice: first audio after 0.68 s, streaming and
24k → 16k resampling work end to end.

## Lesson

A real API key once landed in the tracked `.env.example` during an edit; it was caught before
commit. Keys belong only in the gitignored `.env` (see [security](../reference/security.md)).
