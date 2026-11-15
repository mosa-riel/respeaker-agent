# Step 14 — External speaker, Dutch voice, device wake-sound gate

How-to log (append-only). 2026-11-15.

## Dutch voice

- Mistral has one Dutch preset: "Lotte", `voice_3145` (female), only on Voxtral TTS 3
  (`voxtral-mini-tts-3-0`). On `voxtral-mini-tts-2603` the same id returns 400. Set live with
  `PUT /api/config` `{"tts_model": "voxtral-mini-tts-3-0", "tts_voice_id": "voice_3145"}`.
- `tts_model` is also an add-on option, which `addon-run.py` writes into config.json on every
  boot. A restart that day set it back to 2603 while the voice stayed `voice_3145`, and every
  reply failed with 400. The add-on option now says `voxtral-mini-tts-3-0` too.
- Voxtral TTS 3 FLAC is 24 kHz mono 24-bit, and STREAMINFO has total samples 0 (streamed),
  so `flac_duration_seconds` returns None and the announce timeout falls back to 6 + 25 s.
  The device decodes it fine (checked with device logs: micro_decoder streams and finishes).
- `GET /api/voices` shows only the first page of Mistral's list (10 voices), so Lotte is not
  in the dropdown. Mistral's `GET /v2/audio/voices` takes `page_size`, `page_token`, `type`,
  `gender`, `language` and `query`.

## Volume

- The only output control is the ESPHome media player volume (`volume_max` 1.0 in the
  formatBCE package), and the agent sets `device_volume` 1.0 on every connect, so it is at
  max. The aic3104 DAC has no gain option in the package.
- A Lotte reply peaked at -9 dBFS (RMS -24 dBFS), so a gain or peak normalization of the TTS
  audio could add 6-8 dB. Not done yet.

## Device wake sound in every recording (0.4.27)

- After the external speaker was attached, transcripts became nonsense ("zet de lamellen
  dicht" → "Zet er een melder in.", and the agent put "Melder" on the shopping list).
- The firmware's wake word handler plays the wake sound, waits 300 ms and starts the voice
  assistant (then `lock_beam()` when Beam lock is on). The chime is 0.95 s, so ~0.65 s of it
  is still playing when the mic stream starts. Recordings opened at -3 to -5 dBFS, decaying,
  louder than the speech (-15 to -22 dBFS). STT heard chime plus speech, and the STT
  normalization keyed on the chime. Without a speaker the chime was inaudible.
- With "Wake sound" off (switch on the device) the user found recognition clearly better.
- Fix: the agent ignores the first 600 ms of mic audio (`_DEVICE_CHIME_GATE_MS`) after a wake
  word when the device's "Wake sound" switch is on, the same gate it already used for its own
  chime on `audio_sink`. `DeviceLink` keeps the latest state per entity name (`states`), and
  `VoicePipeline` reads "Wake sound" from it.
- Follow-ups (`start_conversation` after an announcement) also arrive with the wake word
  phrase ("Okay Nabu", flags 1), but the firmware plays no wake sound for them. The agent
  sets `_followup_pending` before the announcement and the next start consumes it, so a
  follow-up is not gated.
- Checked locally: wake with sound on → 600 ms gate; follow-up → none; sound off → none.

## Realtime STT

The user turned `stt_realtime` back on, then found it added nothing; it is off again (live
and in the add-on options).
