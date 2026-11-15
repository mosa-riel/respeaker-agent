# Open items

- **Check the device wake-sound gate on a real recording (0.4.27).** No turn with wake sound
  and speech recorded yet; confirm the recording no longer starts with the chime (-3..-5 dBFS
  decaying). If it does, raise `_DEVICE_CHIME_GATE_MS`.
- **Voice dropdown shows 10 voices.** `GET /api/voices` reads only Mistral's first page; use
  `GET /v2/audio/voices` with `page_size`/`page_token` (and `language`) so presets like
  Lotte (`voice_3145`) appear.
- **TTS loudness.** The device volume is at max; Voxtral replies peak around -9 dBFS. Peak
  normalization (or a gain option) on the announce path could add 6-8 dB.
