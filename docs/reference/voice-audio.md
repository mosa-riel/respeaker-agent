# Reference — Voice & audio layer

> Living doc. The audio formats, the TTS abstraction, and the config that drives
> them. Update alongside `voice.py`/`stt.py`/`tts.py`/`audio.py` changes.

## Audio formats

| Path | Format |
|---|---|
| Device mic → us | 16-bit signed PCM, mono, 16 kHz (ESPHome voice assistant, API audio over TCP) |
| Us → device speaker | 16-bit signed PCM, mono, `tts_out_rate` (default 16 kHz — confirm on device) |
| Voxtral TTS `pcm` | float32 LE, 24 kHz, mono |
| STT upload | WAV (16-bit PCM) wrapping the buffered mic audio |

`audioop` is removed in Python 3.13+ (we run 3.14). All conversion/resampling is
numpy + soxr in `audio.py`. Resample quality is `LQ` — speed/lag beats fidelity
for a voice agent on a small speaker.

## TTS — pluggable engine

`tts.py` exposes one interface so the engine flips by **config alone**:

```
TTSClient.synth_stream(text) -> AsyncIterator[bytes]   # 16-bit mono PCM @ tts_out_rate
make_tts(settings, secrets, trace) -> TTSClient         # picks adapter by tts_provider
```

| provider | adapter | endpoint | body shape | response |
|---|---|---|---|---|
| `voxtral` | `VoxtralTTS` | `POST {base}/audio/speech` | `{input, model, voice_id, response_format, stream}` | JSON `{audio_data: <b64>}`, streamed line-delimited |
| `openai` | `OpenAITTS` | `POST {base}/audio/speech` | `{input, model, voice, response_format}` | binary wav/pcm body |

**Hosted → local swap:** change `tts_base_url` to your localhost server (and
`tts_provider` if it speaks the OpenAI shape). No code change. Set `TTS_API_KEY`
in `.env` if the local server needs a different/empty key (else it reuses
`LLM_API_KEY`).

### Speed posture
- Voxtral asked for `pcm` + `stream:true` → ~0.8s time-to-first-audio. Chunks are
  decoded (float32→int16) and resampled 24k→16k as they arrive, then handed to
  `send_voice_assistant_audio`. `wav` (~3s TTFB) is the slower, header-described
  fallback.
- Long replies should be sentence-chunked upstream (voice loop) so the first
  sentence plays while the rest synthesizes.

## Output routing — device speaker vs host sink

By default TTS plays on the **reSpeaker's own speaker**: the engine's encoded audio
(`tts_voice_format`, FLAC) is served on the LAN audio server and the device fetches the
URL (`voice.py` → announce path; this also drives follow-up mic re-open).

Set **`audio_sink`** to a PulseAudio/PipeWire sink to play TTS on a **host speaker**
instead — e.g. a paired Bluetooth A2DP speaker `bluez_sink.<MAC>.a2dp_sink`, or
`"default"` for the host default. In this mode the voice turn streams int16 PCM straight
into `paplay --raw` (`local_play.py`) and awaits real playback end. The reSpeaker mic is
still the input. **Follow-up is disabled** in local-sink mode (it rides the device
announce path), so wake per turn. As an HA add-on this needs `audio: true` in the
manifest; see `deployment.md`. Optional `bluetooth_control` exposes locked-down
`bluetoothctl` agent tools to scan/connect speakers (needs `host_dbus`) — see step 10 +
`security.md`.

## Diagnosing bad transcriptions — recordings

`save_recordings` (default on) writes each captured utterance — the **exact mic PCM sent
to STT** — as `<config-dir>/recordings/<ts>.wav` + a `.txt` transcript, last 20
(`recordings.py`). The **Live** page lists them with players (`GET /api/recordings`,
`/api/recordings/{name}`; name regex-validated). Play one back: if it sounds clipped /
quiet / cut short → it's capture/VAD (`vad_*`, gain); if it sounds clear but the
transcript is garbage/wrong-language → it's the STT model/language forcing, not the audio.

## Config keys (all non-secret, UI-editable)

`stt_base_url`, `stt_model`, `tts_provider`, `tts_base_url`, `tts_model`,
`tts_voice_id`, `tts_format`, `tts_pcm_rate`, `tts_out_rate`. Keys
(`STT_API_KEY`/`TTS_API_KEY`) are `.env`-only and fall back to `LLM_API_KEY`.

Current voice: `tts_voice_id = bf93b5a8-5759-4c01-8d58-204022a76bea` (user's own
Studio-recorded voice, "Aapje"). The TTS stage settings modal shows a **voice dropdown**
populated from `GET /api/voices` (engine `/audio/voices` with the TTS key; key never
returned). Custom voices are **per-account** — a wrong/stale `api_key` makes the engine
404 `invalid_voice` (looks like "TTS broken"); `voice_id` is required and there are no
Dutch *preset* voices, so Dutch needs a custom-cloned voice on that key.
