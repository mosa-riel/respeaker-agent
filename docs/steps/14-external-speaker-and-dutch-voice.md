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

## 0.4.28: double end chime, MCP reconnects

- Double end chime: after a turn without speech the turn was finalized twice ("no speech
  captured", then the watchdog). The quick no-speech `_finish` reset `_finalizing` within one
  0.5 s watchdog sleep, so the watchdog kept running and fired once the device stopped
  streaming. `_finalize` now cancels the turn's watchdog (unless the watchdog is the caller).
- MCP: after the MCP Screen add-on was updated (twice that afternoon) every screen command
  failed with "Session not found" and red LEDs until the agent was restarted. The connection
  task is now a reconnect loop, and a call on a lost session reconnects and retries once (see
  `reference/deployment.md`). Tested locally: list_pages, restart the MCP server, list_pages
  again → "session lost; reconnecting", connected, the second call succeeds.

## 0.4.29: the model decides whether to keep listening

- The user found that Nabu listened too long after every reply, and once kept re-opening the
  mic while people were talking in the room (each follow-up transcribed the conversation,
  got a reply and opened the next follow-up).
- Now the model decides per reply: the voice loop sends a note with each turn (agent.run
  `note`, not stored in the history) asking it to end the reply with `[VERVOLG]` when it
  expects an answer right away (it asks something, or an open question or explanation). A
  reply that ends in `?` counts too. Device commands (one round with `zeg`) never follow up:
  the reply is announced with `start_conversation=False` and the end chime plays right after.
- A follow-up listens `followup_listen_ms` (2000) for speech; any sound above `vad_threshold`
  in that window (an "eh") extends it to `vad_prespeech_ms`. No sound: the session ends.
- In a follow-up the note also says: not meant for you (people talking, loose words, no
  question or command) → answer only `[NEGEER]`; the session then ends quietly (end chime).
- At most `followup_max` (3) follow-ups per wake word.
- Checked with stubs: command → no follow-up + chime; open answer with [VERVOLG] and a
  question back → follow-up; [NEGEER] in a follow-up → nothing spoken + chime; cap → chime;
  quiet follow-up closes after 2 s, a loud blip extends to 7 s.

## 0.4.30: answer first, the screen follows

- The user wanted Nabu to answer right away and work out the details on the e-paper screen,
  instead of waiting for the whole tool loop. The screen tools (`screen_components`,
  `show_card`, `show_page`, `show_dashboard`, `show_html`, `show_text`, `show_image`) get the
  `zeg` field too. When the model calls a screen tool with `zeg`, the agent hands it to
  `on_say` at once (agent.run): the voice loop synthesizes and announces it while the model
  goes on (catalog → card). When the render tools in a round all succeed and the answer was
  given, the loop ends without a closing LLM round; the session then ends (no follow-up) with
  the end chime.
- The runtime tool rules now say the screen is an extension of the answer: use a card for
  numbers, lists, steps, comparisons, time series, places, routes and directions, always when
  asked to show or draw something, never for plain commands; put the spoken answer in 'zeg'
  of the first screen tool, understandable without the screen.
- Cards come from MCP Screen 0.4.0 (TRMNL Framework building blocks, Vega-Lite charts, PDOK
  maps); see that repo.
- End chime: 0.3 s of silence before it; the external speaker's amplifier swallowed the
  short chime, so the user often did not hear it.
- Checked with stubs: zeg spoken in round 1, stripped before the MCP call, no third round;
  voice speaks once, no follow-up, end chime.
