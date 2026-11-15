# Reference — Voice & audio layer

Audio formats, the STT/TTS abstraction, turn handling and the config that drives them. Update
alongside `voice.py` / `stt.py` / `tts.py` / `audio.py` changes. All keys below are add-on
options or UI settings (`respeaker_agent_addon/config.yaml` has the full list and defaults).

## Audio formats

| Path | Format |
|---|---|
| Device mic → agent | 16-bit signed PCM, mono, 16 kHz (ESPHome voice assistant, API audio over TCP) |
| STT upload | WAV (16-bit PCM) wrapping the buffered mic audio |
| Voxtral TTS `pcm` | float32 LE, 24 kHz, mono → int16 at `tts_out_rate` (host sink path) |
| Agent → device speaker | encoded file (`tts_voice_format`, FLAC) the device fetches by URL from `:8731`; the device rejects WAV |

`audioop` is gone in Python 3.13+; all conversion and resampling is numpy + soxr in
`audio.py`. Resample quality is `LQ`: speed beats fidelity on a small speaker.

## STT

`stt.py` posts the WAV to `{stt_base_url}/audio/transcriptions` (`stt_model`). Options:
`stt_language` forces the language (no auto-detect drift), `stt_prompt` biases short
utterances, `stt_normalize` / `stt_gain_max` boost quiet audio, `stt_extra` passes extra
JSON fields. Opt-in `stt_realtime` uses Mistral's realtime transcription only to decide when a
turn ends (step 13); it is off by default.

## TTS — pluggable engine

`tts.py` exposes one interface, so the engine changes by config alone:

```
TTSClient.synth_stream(text) -> AsyncIterator[bytes]   # 16-bit mono PCM @ tts_out_rate
make_tts(settings, secrets, trace) -> TTSClient         # adapter chosen by tts_provider
```

| provider | adapter | endpoint | body | response |
|---|---|---|---|---|
| `voxtral` | `VoxtralTTS` | `POST {base}/audio/speech` | `{input, model, voice_id, response_format, stream}` | JSON `{audio_data: <b64>}`, streamed line-delimited |
| `openai` | `OpenAITTS` | `POST {base}/audio/speech` | `{input, model, voice, response_format}` | binary wav/pcm body |

**Hosted → local:** point `tts_base_url` at the local server (and `tts_provider` if it speaks
the OpenAI shape). Set `TTS_API_KEY` if it needs a different or empty key; otherwise it
reuses `LLM_API_KEY`.

**Speed:** Voxtral with `pcm` + `stream: true` gives ~0.8 s to first audio; chunks are decoded
and resampled 24k → 16k as they arrive. `wav` (~3 s to first audio) is the slower fallback.

### Voices
- The TTS settings show a voice dropdown from `GET /api/voices` (the engine's `/audio/voices`
  with the TTS key; the key is never returned). It reads only the first page (10 voices).
- Custom (cloned) voices are **per account**: a wrong or stale key makes the engine return
  404 `invalid_voice`, which looks like "TTS broken".
- Mistral's Dutch preset "Lotte" (`voice_3145`) needs `tts_model = voxtral-mini-tts-3-0`; on
  `voxtral-mini-tts-2603` it returns 400. `tts_model` is an add-on option rewritten on every
  boot, so change it there, not only in the UI.
- Voxtral TTS 3 FLAC is 24 kHz mono 24-bit without a sample count, so
  `flac_duration_seconds` returns None (the announce timeout falls back to a default); the
  device plays it fine.

## Output routing — device speaker or host sink

By default replies play on the **reSpeaker's speaker**: the encoded audio is served on the LAN
audio server and announced to the device by URL. This path also re-opens the mic for a
follow-up.

Set **`audio_sink`** to a PulseAudio sink to play on a **host speaker** instead, e.g. a paired
Bluetooth speaker `bluez_sink.<MAC>.a2dp_sink`, or `"default"`. The turn then streams int16
PCM into `paplay --raw` (`local_play.py`) and awaits the real end of playback; the reSpeaker
mic stays the input. `bt_lead_silence_ms` plays silence first so a suspended A2DP sink wakes
without clipping the start; `wake_chime` plays a wake cue on that sink. **Follow-up is off in
local-sink mode** (it rides the device announce path), so every turn needs the wake word.
The add-on needs `audio: true`; opt-in `bluetooth_control` adds fenced `bluetoothctl` tools
(needs `host_dbus`), see [deployment](deployment.md) and [security](security.md).

## Turn detection

The firmware streams the mic continuously and never signals the end of speech, so the agent
decides:

- `vad_engine: micro` (default) uses pymicro-vad (HA's own voice VAD); a block counts as
  speech when the model says so (`vad_speech_prob`) **and** the RMS is above `vad_threshold`,
  so fans, hum and distant voices do not keep the mic open. It lags ~0.5 s, hence a 1 s
  pre-roll before the detected onset. The model is first loaded in a subprocess; if that
  crashes, the agent falls back to `energy` (RMS above `vad_threshold` only).
- The turn ends after `vad_silence_ms` of non-speech, at `vad_max_ms` at the latest, or
  without a transcript if nothing is said within `vad_prespeech_ms`. Less than 250 ms of
  speech before a pause is ignored as a blip.
- A per-turn watchdog finalizes a turn whose audio stopped arriving (1.5 s) or that runs past
  `vad_max_ms` + 2 s.

## Wake sound and the mic gate

The device's wake sound (switch "Wake sound") is a 0.95 s chime; the firmware starts the mic
stream 300 ms into it. With an external speaker the rest of it lands in the recording, louder
than the speech, and garbles STT. So after a wake word, with "Wake sound" on (read from
`DeviceLink.states`), the agent ignores the first 600 ms of mic audio
(`_DEVICE_CHIME_GATE_MS`). Follow-ups also carry the wake word phrase but get no chime;
`_followup_pending`, set before the announcement, keeps them ungated. With `wake_chime` +
`audio_sink` the agent plays its own chime and gates for that instead.

## Follow-up (mic re-open after a reply)

With `voice_followup` on, the model decides per reply whether to keep listening: `[VERVOLG]` at
the end of its reply (or a reply ending in `?`) re-opens the mic via the announce API
(`start_conversation=True`); otherwise the reply plays and the end chime follows. Markers are
stripped before TTS. A follow-up waits `followup_listen_ms` for speech, extended to
`vad_prespeech_ms` by any sound above `vad_threshold`. In a follow-up the model answers
`[NEGEER]` when the speech was not meant for it, which ends the session quietly. At most
`followup_max` follow-ups per wake word. The rules travel as a per-turn note (`_NOTE` /
`_NOTE_FOLLOWUP` in `voice.py`), not in the stored system prompt. Device commands answered in
one round never follow up. The end chime has 0.3 s of lead-in silence so an external
speaker's amplifier does not swallow it.

## Answer first, screen and progress

- **Early answer.** `ha_call_service` and the screen tools carry a `zeg` field (the sentence to
  speak). A successful service call with `zeg` is spoken without a second LLM round. The
  first screen call's `zeg` is spoken at once via `agent.run(on_say=...)` →
  `VoicePipeline._announce` while the model builds the card; once the answer is spoken and a
  card in that round rendered, the turn ends without a closing round
  (`AgentResult.spoken_early`).
- **Progress.** While the agent works, `on_progress` lines are spoken: the model's own text
  next to a tool call, or after `_QUIET_S` (7 s) of silence a line naming the current step;
  at most `_PROGRESS_MAX` (3) per question. All announcements go through `_say_lock`, so they
  play in order. After a progress line the agent re-sends `STT_VAD_END` so the LED ring shows
  "thinking" again.
- **Interrupt and time limit.** A new wake word cancels the running question (so does a
  progress line cut short by the firmware). A question gets `_TURN_BUDGET_S` (45 s), then a
  spoken apology; other errors are spoken too.
- Markdown is stripped (`_speakable`) from everything that reaches TTS.

## Diagnosing transcriptions and turns

- **Recordings.** `save_recordings` (default on) writes each captured utterance, the exact mic
  PCM sent to STT, as `<config-dir>/recordings/<ts>.wav` + a `.txt` transcript, last 20
  (`recordings.py`). The **Live** page lists them (`GET /api/recordings`,
  `/api/recordings/{name}`; name regex-validated). Clipped, quiet or cut-short audio points to
  capture/VAD; clear audio with a wrong transcript points to the STT model or language.
- **Turn log.** Every voice turn is appended to `<config-dir>/turns.jsonl` (`turns.py`, ~2000
  turns): heard text, tool calls (arguments and results shortened), spoken lines with their
  time since the wake, the model's final text, errors, outcome, duration and add-on version.
  It is built from the turn's trace events when the turn ends, so it survives restarts.
  `GET /api/turns?limit=50&since=<epoch>` returns it; `scripts/turn-report.py` lists the
  turns with flags for a closer look.

## Config keys (non-secret)

STT: `stt_base_url`, `stt_model`, `stt_language`, `stt_prompt`, `stt_normalize`,
`stt_gain_max`, `stt_extra`, `stt_realtime*`. TTS: `tts_provider`, `tts_base_url`,
`tts_model`, `tts_voice_id`, `tts_format`, `tts_pcm_rate`, `tts_out_rate`,
`tts_voice_format`, `tts_audio_port`, `tts_audio_host`. Voice: `voice_enabled`,
`voice_followup`, `voice_end_chime`, `device_volume`, `tts_gain_db`, `vad_*`, `followup_*`, `audio_sink`,
`bt_lead_silence_ms`, `save_recordings`. Keys (`STT_API_KEY` / `TTS_API_KEY`) are env-only and
fall back to `LLM_API_KEY`.

## Output loudness

The reSpeaker's media player volume (`device_volume`) is its only output control and is set at
max on every connect. Replies are made loud in the audio itself: each FLAC reply is peak-normalized
to -1 dBFS and then raised by `tts_gain_db` dB (0–12, default 4) through a soft limiter
(`audio.louder_flac`; samples above 0.6 bend towards 0.95 instead of clipping). Raise it if the
speaker is still too quiet; above ~8 dB the limiter starts to work audibly. The end chime is
generated near full scale (0.85).
