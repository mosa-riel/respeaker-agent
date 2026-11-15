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

**Turn log.** Every voice turn is appended to `<config-dir>/turns.jsonl` (`turns.py`, ~2000
turns kept): heard text, tool calls (arguments and result shortened), spoken lines with their
time since the wake, the model's final text, errors, outcome and duration. It is built from the
turn's trace events when the turn ends, so it survives restarts where the trace does not.
`GET /api/turns?limit=50&since=<epoch>` returns it; `scripts/turn-report.py` (stdin or a file)
lists the turns with flags for a closer look.

## Config keys (all non-secret, UI-editable)

`stt_base_url`, `stt_model`, `tts_provider`, `tts_base_url`, `tts_model`,
`tts_voice_id`, `tts_format`, `tts_pcm_rate`, `tts_out_rate`. Keys
(`STT_API_KEY`/`TTS_API_KEY`) are `.env`-only and fall back to `LLM_API_KEY`.

Current voice (since 2026-11-15): Mistral's Dutch preset "Lotte", `tts_voice_id =
voice_3145`, which needs `tts_model = voxtral-mini-tts-3-0` (400 on `voxtral-mini-tts-2603`).
`tts_model` is an add-on option and is rewritten on every boot, so it must be set there too.
Earlier voices were custom (per-account) voices, e.g. `bf93b5a8-…` ("Aapje", the user's own
Studio recording). The TTS stage settings modal shows a **voice dropdown**
populated from `GET /api/voices` (engine `/audio/voices` with the TTS key; key never
returned); it shows only the first page (10 voices), so Lotte is not in it. Custom voices are
**per-account** — a wrong/stale `api_key` makes the engine 404 `invalid_voice` (looks like
"TTS broken"). Voxtral TTS 3 FLAC is 24 kHz mono 24-bit without a sample count, so
`flac_duration_seconds` returns None; the device plays it fine.

## Wake sound and the mic gate

The device's own wake sound (switch "Wake sound") is a 0.95 s chime; the firmware starts the
mic stream 300 ms into it. With a speaker attached the rest of it lands in the recording,
louder than the speech, and garbles STT. So after a wake word, with "Wake sound" on (read from
`DeviceLink.states`), the agent ignores the first 600 ms of mic audio (`_DEVICE_CHIME_GATE_MS`).
Follow-ups (`start_conversation` after a reply) carry the wake word phrase too but get no
chime; `_followup_pending`, set before the announcement, keeps them ungated. With
`wake_chime` + `audio_sink` the agent plays its own chime and gates for that instead.

## Follow-up (mic re-open after a reply)

With `voice_followup` on, the model decides per reply whether to listen on: `[VERVOLG]` at the
end of its reply (or a reply ending in `?`) re-opens the mic via the announce API
(`start_conversation=True`); otherwise the reply plays and the end chime follows. Markers are
stripped before TTS. A follow-up waits `followup_listen_ms` (2000) for speech, extended to
`vad_prespeech_ms` by any sound above `vad_threshold`; in a follow-up the model answers
`[NEGEER]` when the speech was not meant for it, which ends the session quietly. At most
`followup_max` (3) follow-ups per wake word. The rules ride along as a per-turn note
(`_NOTE` / `_NOTE_FOLLOWUP` in voice.py), not in the stored system prompt.

## Early answer with the screen (0.4.30)

Screen tools carry `zeg`; the first screen call's `zeg` is spoken immediately via
`agent.run(on_say=...)` → `VoicePipeline._announce` (no follow-up) while the model builds
the card. A round of successful render calls then ends the loop without a closing LLM
round (`AgentResult.spoken_early`). The end chime has 0.3 s of lead-in silence so an
external speaker's amplifier does not swallow it.

## Progress, interrupting, time limit (0.4.31)

While the agent works, `on_progress` lines are announced (the model's text next to tool
calls; after 7 s of silence a line naming the current step; max 3). All announcements go
through `_say_lock`. "Okay Nabu" during a question cancels it (`_interrupt`); so does a
progress line cut short by the firmware. A question gets 45 s (`_TURN_BUDGET_S`), then a
spoken apology; errors are spoken too.
