# Step 13 — Realtime turn detection (opt-in) and STT provider check

How-to log (append-only). 2026-11-01.

## Why

The end of an utterance was a fixed rule: 800 ms below `vad_threshold` = done. A thinking
pause cut the user off; noise above the threshold kept the mic open. The reSpeaker firmware
streams the mic continuously and never sends an end, so the agent has to decide.

## Mistral realtime transcription

`wss://api.mistral.ai/v1/audio/transcriptions/realtime?model=…`, Bearer auth. Not all of it
is in the public docs; the protocol below comes from the `mistralai[realtime]` 3.6 SDK.

- client: `session.update` (`audio_format` pcm_s16le/16 kHz, `language`, `context_bias`,
  `turn_detection` {start_threshold, eager_end_threshold, end_threshold, min_silence_ms,
  end_timeout_ms}), `input_audio.append` (base64), `input_audio.flush`, `input_audio.end`
- server: `session.created`, `transcription.text.delta`, `transcription.language`,
  `turn.start`, `turn.eager_end`, `turn.resume`, `turn.end`, `transcription.done`, `error`
- `voxtral-mini-transcribe-realtime-2602` rejects `context_bias`; `…-realtime-3` takes it.

Replaying saved recordings at real-time pace (`-3`, nl, word list, min_silence 500 ms):
`turn.end` came 1–2.6 s after the last word (once 5.4 s), with eager_end/resume flapping
in between — it waits through pauses. Its text was not better than batch `transcribe-3`
(lost 2 of 8, won 1).

## Design: hybrid, behind `stt_realtime` (default off)

`realtime_stt.RealtimeTurn` streams each mic chunk while the user talks and only decides
WHEN the turn ends (`turn.end` → `VoicePipeline._on_realtime_end` → finalize). The final
text still comes from `stt_model` on the buffered audio; the realtime text is traced
("realtime einde beurt: …") for comparison. On any connect/stream error the turn falls back
to the silence rule; `vad_max_ms` and `vad_prespeech_ms` still cap the turn.
Options: `stt_realtime`, `stt_realtime_model`, `stt_realtime_min_silence_ms` (add-on
options, also live via `PUT /api/config`). Uses `websockets` (already in the lock via
uvicorn, now an explicit dependency).

Checked offline against a fake realtime server (keeps listening through a 1.2 s pause,
ends on `turn.end`; unreachable server falls back to the silence rule) and live against
Mistral with saved recordings.

## STT provider check (hostyourai)

hostyourai serves `voxtral-mini-2602` and `whisper-large-v3` for transcription. On 12
recordings with known text and 10 silent ones: Mistral `transcribe-3` + word list 14% WER,
9/12 perfect, 0 hallucinations, 0.32 s; hostyourai voxtral-mini-2602 37%, 5/12, 0.44 s (no
`context_bias` support); whisper-large-v3 34%, 6/12, invented text on 8 of 10 silent clips,
0.46 s. STT stays on Mistral `transcribe-3`.

## First live use (0.4.21)

- Two commands: both transcribed right; `turn.end` came ~2.7 s after the last word.
- "Zet alle verlichting in de keuken aan … en de eettafel ook": the first sentence was
  complete, ~2 s of silence followed, the turn ended, and the second part was spoken while
  the agent processed (mic not listening). An earlier end (on `turn.eager_end`) would make
  this worse, so that idea was dropped.
- Bug: a follow-up window never closed. The realtime model reported `turn.start` (likely
  the tail of the reply), which blocked the "no speech" exit; then the device stopped
  streaming, and the VAD only runs on incoming audio — no RUN_END, no end chime, until
  Mistral timed out 34 s later. Fixed with a per-turn watchdog (no audio for 1.5 s, or
  longer than vad_max_ms + 2 s → finalize) and a bound on the realtime-only wait
  (prespeech + 3 s).

## Speed pass (0.4.22)

Goal set by the user: question → action as fast as possible.

- Realtime turn detection switched off (option kept): it cost ~2.7 s per command and its
  text is not used.
- One LLM round for device commands. Qwen3 never puts text next to tool calls, so
  `ha_call_service` gets an extra `zeg` field ("sentence to speak if this works"); the
  agent strips it before the MCP call and, when every call is a service call that
  succeeded, speaks it without a second round. A failed call still gets the second
  round. Benchmark (Qwen3-235b, 12 questions × 2): device commands 1.0–1.7 s instead of
  1.7–2.9 s, median 1.3 s instead of 2.1 s.
- Silero speech detection (0.4.23): `vad_engine` silero (default) uses pysilero-vad
  (MIT, ggml Silero v6.2, no onnxruntime) on 512-sample blocks; `vad_speech_prob` 0.5.
  On saved recordings it marks the speech like the energy rule does, and on synthetic fan
  rumble / 120 Hz hum at ~-33 dBFS (above the energy threshold) it finds no speech at all,
  so room noise no longer keeps the mic open. ~0.2-0.3 ms CPU per 32 ms block. Silero
  holds ~0.2 s past the last sound, so `vad_silence_ms` went from 800 to 500. Falls back
  to the energy rule when the package is missing (`vad_engine: energy` forces it).
- Silero crashed the agent on the HA host (0.4.23): its ggml build needs AVX and the host is
  an Intel Celeron J4025 (SSE4.2 only). The process died with no Python error and the add-on
  restart-looped until `vad_engine` was set back to energy. Reproduced locally with
  `qemu-x86_64 -cpu Westmere` (core dump). Replaced by pymicro-vad (Apache-2.0, the VAD of
  HA's own voice pipeline), which runs under the same emulation (0.4.24). It detects speech
  ~0.46 s late at both ends, so the pre-roll before the onset is 1 s and `vad_silence_ms`
  is 300 (≈0.75 s after the last sound). It ignores the synthetic fan rumble and hum too.
  The model is first loaded in a subprocess; if that crashes, the agent falls back to the
  energy rule instead of restart-looping.
- First live test on micro (0.4.24): "zet eettafel aan" — end ~0.7 s after the last word,
  lamp ~2.7 s, reply audible ~4.0 s. One turn stayed open ~3 s: after the command the model
  kept hearing speech-like sound at -36..-42 dBFS (the user talks at -20..-25).
- 0.4.25: speech = model says speech AND RMS > `vad_threshold` (600, ~-35 dBFS). Replaying
  that turn now ends after 1.9 s of audio instead of 4.6 s; the user's own recording 15 dB
  down (a TV across the room) no longer opens a turn.
- The clock moved from the system prompt to this turn's question ("(Het is nu …)"), so the
  system prompt + tools prefix is stable: hostyourai reported 6720 of ~6760 prompt tokens
  cached with a stable prefix. `llm-rsp` trace events now carry the provider's `usage`.
- Device controls in the list (0.4.26): "Zet de lamellen open" raised the venetian blind
  (`open_cover`) instead of tilting it. The user wanted the model to read this off the
  controls, not a house rule in the prompt. HomeContext now adds, from HA's
  `supported_features`, how a tiltable cover is operated ("omhoog/omlaag = open_cover/
  close_cover, open/dicht = kantelen met open_cover_tilt/close_cover_tilt, kanteling in %
  met set_cover_tilt_position"). Dry run on Qwen: open → tilt open, omhoog → open_cover,
  dicht → tilt closed, half open → tilt 50 %, helemaal naar beneden → close_cover; a
  cover without tilt keeps "open" = open_cover.
