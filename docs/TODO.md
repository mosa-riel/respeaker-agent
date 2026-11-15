# Open items

- **Check the device wake-sound gate on a real recording (0.4.27).** Confirm that a turn
  recorded with the wake sound on no longer starts with the chime (-3..-5 dBFS, decaying). If
  it does, raise `_DEVICE_CHIME_GATE_MS`.
- **Voice dropdown shows only 10 voices.** `GET /api/voices` reads only the first page of
  Mistral's list; use `GET /v2/audio/voices` with `page_size`/`page_token` (and `language`)
  so presets such as Lotte (`voice_3145`) appear.
- **`ha_call_service` domain allowlist** in `mcp-homeassistant` (see
  [security](reference/security.md)).
