# Step 13 — Turn detection, speech detection, speed

**Versions:** 0.4.20 – 0.4.26 · **Date:** 2026-11-01
**Goal:** end the user's turn at the right moment, and get from question to action as fast as
possible.

## Why

The end of an utterance was a fixed rule: 800 ms below `vad_threshold`. A thinking pause cut
the user off, and noise above the threshold kept the mic open. The firmware never signals the
end of speech, so the agent has to decide.

## Mistral realtime transcription (opt-in, off)

`wss://api.mistral.ai/v1/audio/transcriptions/realtime?model=…`, Bearer auth. Part of the
protocol is not in the public docs; this comes from the `mistralai[realtime]` 3.6 SDK:

- client: `session.update` (`audio_format` pcm_s16le/16 kHz, `language`, `context_bias`,
  `turn_detection` {start_threshold, eager_end_threshold, end_threshold, min_silence_ms,
  end_timeout_ms}), `input_audio.append` (base64), `input_audio.flush`, `input_audio.end`
- server: `session.created`, `transcription.text.delta`, `transcription.language`,
  `turn.start`, `turn.eager_end`, `turn.resume`, `turn.end`, `transcription.done`, `error`
- `voxtral-mini-transcribe-realtime-2602` rejects `context_bias`; `…-realtime-3` accepts it.

Design (`realtime_stt.RealtimeTurn`, behind `stt_realtime`): stream each mic chunk and use
`turn.end` only to decide when the turn ends; the final text still comes from `stt_model` on
the buffered audio. On any error the silence rule takes over; `vad_max_ms` and
`vad_prespeech_ms` still cap the turn. Options: `stt_realtime`, `stt_realtime_model`,
`stt_realtime_min_silence_ms`.

Findings: `turn.end` came 1–2.6 s after the last word (once 5.4 s), and its text was no better
than batch transcription. Live, a command split by a ~2 s pause ended after the first half,
so an earlier end (on `turn.eager_end`) would be worse. Since it cost ~2.7 s per command and
its text is unused, it is **off by default** (0.4.22).

A follow-up window once never closed: the realtime model reported `turn.start` on the
reply's tail, then the device stopped streaming, and the VAD only runs on incoming audio. Fix
(0.4.21): a per-turn watchdog (no audio for 1.5 s, or longer than `vad_max_ms` + 2 s →
finalize) and a bound on the realtime-only wait.

## STT provider check

12 recordings with known text plus 10 silent ones: Mistral `transcribe-3` with a word list
14% WER, 9/12 perfect, no hallucinations, 0.32 s; hostyourai `voxtral-mini-2602` 37%, 5/12,
0.44 s (no `context_bias`); `whisper-large-v3` 34%, 6/12, invented text on 8 of 10 silent
clips, 0.46 s. STT stays on Mistral.

## Speech detection: model-based VAD (0.4.23 – 0.4.25)

- 0.4.23 used Silero (pysilero-vad, ggml) with `vad_engine`. It ignores fan rumble and hum
  above the energy threshold. But its ggml build needs **AVX**; on a host CPU without it (an
  Intel Celeron J4025, SSE4.2 only) the process died without a Python error and the add-on
  restart-looped. Reproduced with `qemu-x86_64 -cpu Westmere`.
- 0.4.24 switched to **pymicro-vad** (Apache-2.0, the VAD of HA's own voice pipeline), which
  runs without AVX. It detects speech ~0.46 s late at both ends, so the pre-roll before the
  onset is 1 s and `vad_silence_ms` can be short. The model is first loaded in a subprocess;
  if that crashes, the agent falls back to the energy rule instead of restart-looping.
- 0.4.25: speech = the model says speech **and** RMS > `vad_threshold`. Faint speech-like
  sound (-36..-42 dBFS, against speech at -20..-25) no longer keeps a turn open, and a voice
  15 dB down (a TV across the room) no longer opens one.

## One LLM round for device commands (0.4.22)

Qwen3 never puts text next to tool calls, so `ha_call_service` got an extra `zeg` field (the
sentence to speak if it works). The agent strips it before the MCP call and, when every call
in the round is a successful service call, speaks it without a second round; a failure still
gets one. Benchmark (Qwen3-235B, 12 questions × 2): device commands 1.0–1.7 s instead of
1.7–2.9 s, median 1.3 s instead of 2.1 s.

## Prompt caching (0.4.25)

The clock moved from the system prompt into the turn's question ("(Het is nu …)"), so the
system prompt + tools prefix is stable and gets cached by the provider (~99% of prompt tokens
in a test). `llm-rsp` trace events carry the provider's `usage`.

## Device controls in the list (0.4.26)

Asked to open the slats of a venetian blind, the model raised the blind (`open_cover`)
instead of tilting it. Decision: derive the behaviour from the device, not from a house rule
in the prompt. HomeContext now adds, from HA's `supported_features`, how a tiltable cover is
operated: up/down = `open_cover`/`close_cover`, open/closed = `open_cover_tilt`/
`close_cover_tilt`, tilt in % = `set_cover_tilt_position`. A cover without tilt keeps
open = `open_cover`.
