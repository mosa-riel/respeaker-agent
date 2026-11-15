# Open items

- **Stale turn watchdog (seen 2026-11-15, 0.4.27).** After a turn with no speech, the turn is
  finalized twice: "no speech captured", then 1-2 s later "turn closed by watchdog (audio
  stopped or too long)" and "no speech captured" again, with the end chime played twice.
  Likely cause: `_watch_turn` loops `while not self._finalizing`, and a fast `_finish`
  (no-speech path) calls `_reset()` (`_finalizing = False`) within one 0.5 s sleep, so the old
  watchdog keeps running and fires once the device stops streaming. Fix idea: cancel
  `self._watchdog` in `_finalize`, or give each turn an id the watchdog checks.
- **Check the device wake-sound gate on a real recording (0.4.27).** No turn with wake sound
  and speech recorded yet; confirm the recording no longer starts with the chime (-3..-5 dBFS
  decaying). If it does, raise `_DEVICE_CHIME_GATE_MS`.
- **Voice dropdown shows 10 voices.** `GET /api/voices` reads only Mistral's first page; use
  `GET /v2/audio/voices` with `page_size`/`page_token` (and `language`) so presets like
  Lotte (`voice_3145`) appear.
- **TTS loudness.** The device volume is at max; Voxtral replies peak around -9 dBFS. Peak
  normalization (or a gain option) on the announce path could add 6-8 dB.
