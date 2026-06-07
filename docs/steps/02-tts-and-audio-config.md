# Step 02 — Pluggable TTS + audio layer + config surface

**Date:** 2026-06-07
**Goal:** Phase-2 groundwork — a swappable text-to-speech path (hosted Voxtral ↔
local), the audio conversions the device needs, and a UI/config surface for the
STT/TTS/LLM endpoints.

## Decisions made (with the user)

- **TTS engine = Mistral Voxtral TTS** (`voxtral-mini-tts-2603`), not Piper.
  Reasons: same provider as STT+chat (one key, one auth), Dutch support, ~0.8s
  time-to-first-audio on `pcm`, **zero-shot voice cloning**, and **open weights
  (CC BY-NC 4.0)** → self-hostable later. Released 2026-03-26.
  - Piper isn't slow (it's real-time), but its Dutch voices are weak — the user
    disliked `ronnie`. Voxtral fixes the voice problem via cloning.
- **Voice = the user's own Studio-recorded voice**, `voice_id`
  `bf93b5a8-5759-4c01-8d58-204022a76bea`.
- **Speed over quality.** Request `pcm` (float32 LE @ 24 kHz) with `stream:true`,
  decode + resample to the device rate (16 kHz) as chunks arrive. soxr quality
  `LQ`. wav (~3s TTFB) is the slower fallback.
- **Engine must be swappable by config alone** — flip `tts_base_url`
  (+ `tts_provider`) to point at a localhost server; no code change.

## What was built

- `audio.py` — numpy/soxr conversions (no `audioop`; gone in Python 3.13+, we're
  on 3.14). `float32le_to_int16`, `resample_int16` (LQ), `wav_to_int16_mono`,
  `int16_to_wav_bytes` (for the STT upload later).
- `tts.py` — `TTSClient` ABC with `synth_stream(text) -> AsyncIterator[bytes]`
  (16-bit mono PCM @ `tts_out_rate`). Adapters: `VoxtralTTS` (POST
  `{base}/audio/speech`, streams base64 `audio_data`), `OpenAITTS` (generic
  `/audio/speech` binary body, for the local-swap case). `make_tts()` selects by
  `tts_provider`.
- `config.py` — new non-secret `Settings`: `stt_base_url/stt_model`,
  `tts_provider/tts_base_url/tts_model/tts_voice_id/tts_format/tts_pcm_rate/
  tts_out_rate`. New `Secrets`: `stt_api_key`/`tts_api_key`, both falling back to
  `LLM_API_KEY` (same Mistral account) — set separately only for a local server.
- `web.py` — `PUT /api/config` allowlist extended with the STT/TTS/LLM string
  fields + ranged int fields (`tts_pcm_rate`, `tts_out_rate`). `tts_provider`
  validated to `voxtral|openai`. `mcp_servers` and `web_host/web_port` still
  excluded (subprocess-exec / network-bind — see security doc).
- `static/index.html` — Configuration card gained STT/TTS rows. Secrets never
  rendered or posted.
- deps: `httpx`, `numpy`, `soxr` (uv.lock updated).

## Verified

- `make_tts` against the **real** Voxtral API with the user's `voice_id`:
  `TTFB=0.68s, 7 chunks, 135680 PCM bytes ≈ 4.24s @ 16000 Hz`. Streaming + 24k→16k
  resample confirmed end-to-end.
- App imports clean; all new config keys round-trip through `Settings`.

## Security note

- A real Mistral key briefly landed in `.env.example` (tracked template) via an
  edit; caught before commit. Moved to `.env` (gitignored), example restored to
  placeholders. Key was never committed/pushed. See `reference/security.md`.

## Open / next

- STT client (`stt.py`): buffer device mic PCM → wav → `/audio/transcriptions`.
- LLM tool-calling loop (`agent_loop.py`): verify the tool ran, don't trust "done".
- Voice pipeline (`voice.py`): `subscribe_voice_assistant` (API_AUDIO),
  wake→STT→LLM→TTS→`send_voice_assistant_audio`. **Needs HA's pipeline for this
  device stopped first** (only one voice handler allowed).
- Confirm the device speaker path's expected sample rate (assumed 16 kHz); adjust
  `tts_out_rate` during device test if pitch is off.
</content>
</invoke>
