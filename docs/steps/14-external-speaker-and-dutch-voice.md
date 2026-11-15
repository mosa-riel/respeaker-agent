# Step 14 — External speaker, Dutch voice, answer-first UX

**Versions:** 0.4.27 – 0.4.38 · **Date:** 2026-11-15
**Goal:** good sound and recognition with an external speaker on the reSpeaker, a Dutch
preset voice, and a voice UX that answers quickly, shows details on the e-paper screen and
does not keep listening needlessly. Current behaviour is summarised in
[voice-audio](../reference/voice-audio.md).

## Dutch voice and volume

- Mistral's only Dutch preset, "Lotte" (`voice_3145`), works only on Voxtral TTS 3
  (`voxtral-mini-tts-3-0`); on `voxtral-mini-tts-2603` the same id returns 400.
- **Lesson:** `tts_model` is also an add-on option and `addon-run.py` rewrites it on every
  boot. Setting it only via `PUT /api/config` lasted until the next restart, after which every
  reply failed with 400. Change models in the add-on options.
- Voxtral TTS 3 FLAC is 24 kHz mono 24-bit with total samples 0 (streamed), so
  `flac_duration_seconds` returns None and the announce timeout falls back to 6 + 25 s. The
  device decodes it fine.
- The only output control is the ESPHome media player volume (`volume_max` 1.0), and the
  agent sets `device_volume` on every connect; the aic3104 DAC has no gain option in the
  firmware package. Replies peak around -9 dBFS, so normalization could add 6–8 dB
  ([TODO](../TODO.md)).

## Device wake sound in every recording (0.4.27)

- With an external speaker attached, transcripts turned to nonsense (a command was misheard
  and ended up on the shopping list).
- Cause: the firmware plays the 0.95 s wake sound, waits 300 ms and starts the voice
  assistant, so ~0.65 s of chime opens every recording at -3 to -5 dBFS, louder than the
  speech (-15 to -22 dBFS). STT heard both, and its normalization keyed on the chime. Without
  an external speaker the chime was inaudible to the mic.
- Fix: with the device's "Wake sound" switch on, the agent ignores the first 600 ms of mic
  audio after a wake word (`_DEVICE_CHIME_GATE_MS`), the same gate as for its own chime on
  `audio_sink`. `DeviceLink` keeps the latest state per entity (`states`). Follow-ups also
  arrive with the wake word phrase but without a chime; `_followup_pending` keeps them
  ungated.
- Realtime STT was tried again here and added nothing; it stays off.

## Reliability fixes (0.4.28)

- **Double end chime:** a turn without speech was finalized twice (no-speech exit, then the
  watchdog). `_finalize` now cancels the turn's watchdog unless the watchdog is the caller.
- **MCP session loss:** after an MCP add-on update every call failed with "Session not found"
  until the agent restarted. Connections are now a reconnect loop and a call on a lost session
  reconnects and retries once ([deployment](../reference/deployment.md)).

## The model decides whether to keep listening (0.4.29)

Problem: the mic stayed open too long after every reply, and with people talking in the room
each follow-up transcribed the conversation, answered it and opened the next one.

- A per-turn note (not stored in history) asks the model to end the reply with `[VERVOLG]`
  when it expects an immediate answer; a reply ending in `?` counts too. Device commands
  answered in one round never follow up.
- A follow-up listens `followup_listen_ms` (2000) for speech; any sound above
  `vad_threshold` extends it to `vad_prespeech_ms`.
- In a follow-up, speech not meant for the agent is answered with `[NEGEER]`, which ends the
  session quietly. At most `followup_max` (3) follow-ups per wake word.

## Answer first, the screen follows (0.4.30)

- The screen tools (`screen_components`, `show_card`, `show_page`, `show_dashboard`,
  `show_html`, `show_text`, `show_image`) get the `zeg` field too. The first screen call's
  `zeg` is spoken at once (`on_say`) while the model builds the card; when the round's render
  calls succeed, the turn ends without a closing LLM round.
- Rules: the screen extends the answer. Use a card for numbers, lists, steps, comparisons,
  time series, places, routes and directions, and whenever asked to show something; never for
  plain commands. The spoken answer must make sense without the screen.
- Cards come from MCP Screen 0.4.0 (TRMNL framework building blocks, Vega-Lite charts, PDOK
  maps).
- The end chime gets 0.3 s of lead-in silence; an external speaker's amplifier swallowed the
  short chime.

## Progress speech, interrupting, time limit (0.4.31 – 0.4.32)

Problem: long silent waits. The time is in LLM rounds (2–8 s each), not in the tools: a
weather card took five rounds, and one route question ran ~90 s through a page fetcher that
cannot read JSON, web search and a hung LLM call. Text the model wrote next to tool calls was
never spoken.

- `agent.run(on_progress)`: the model's text next to a tool call is spoken at once (except
  for service calls, where it is the answer); after `_QUIET_S` (7 s) of silence a line names
  the current step; at most `_PROGRESS_MAX` (3), never the same line twice. Announcements are
  serialized by `_say_lock`.
- A new wake word cancels the running question, its queued speech and the conversation; so
  does a progress line cut short by the firmware. A cancelled turn leaves the device alone.
- `_TURN_BUDGET_S` (45 s), then a spoken apology; other failures are spoken too.
- The firmware resets the LEDs to idle after every announcement, so after a progress line the
  agent re-sends `STT_VAD_END` (thinking) unless the turn is over or speech is playing.
- LLM comparison with the agent's real prompt and tools, first reply with a tool call:
  Mistral Medium 0.6 s, Qwen3-235B 0.7–2.0 s, Mistral Small 0.5–1.7 s (but it read a lamp's
  state instead of switching it), Mistral Large 1.8–2.6 s. A full `show_card` answer: Medium
  2.4–2.6 s (350–420 tokens), Qwen 13–18 s (2900–3200 tokens), Large 11–28 s.

## No empty promises (0.4.33 – 0.4.36)

- A final text that promises screen work (`_PROMISE`) while no screen tool ran is spoken as
  the answer and the model is pushed once (`_NUDGE`) to make the card, built while the
  sentence plays. The same nudge applies when the model looked something up and answered
  without a card (0.4.36): every information question gets a card; device commands and small
  talk do not.
- Call `screen_components` in the first round, together with the lookups, when a card is
  likely.
- Progress lines vary and fit the step ("Ik zoek de route naar Utrecht op.", "Even kijken wat
  het weer morgen doet.", "Momentje."); the model is asked to add its own short sentence next
  to slow tool calls.
- **Where home is:** the context starts with the home town (from `zone.home` via PDOK reverse
  geocoding) and coordinates, not the street address; "thuis", "hier" or no starting point
  means the house. mcp-geo treats those words the same.
- **Memory:** a voice session keeps its last three questions and answers
  (`ConversationStore.keep_recent`, TTL 30 min), also when interrupted, so "dat", "daar" or
  "en morgen?" resolve. The agent's own nudges are not stored.
- **Search at most twice** per question, then answer with what was found and say when it is
  uncertain (one opening-hours question had taken ten searches).
- New MCP servers: mcp-geo (places, routes; OpenStreetMap Nominatim with alternatives
  instead of hand-tuned ranking) and mcp-staan (staan.ai search, market en-gb as there is no
  Dutch market). Websearch was disabled in the agent. Prefer general improvements over fixes
  tuned to test questions.

## Stop after the answer, honest not-found, turn log (0.4.37)

- A turn now ends once the answer was spoken and a card rendered in that round, whatever else
  ran beside it; otherwise a later "Even kijken …" became a second answer nobody heard.
- When a tool finds nothing or only a similar name, say so (also on the card) and ask if
  needed; don't repeat the search in other spellings. (A misheard street was searched four or
  five ways, then a city centre was shown as if it were the street.) mcp-geo 0.1.2 falls back to a
  fuzzy PDOK search and marks similar names.
- **Turn log:** every voice turn goes to `/data/turns.jsonl` (`turns.py`), served by
  `GET /api/turns`, with `scripts/turn-report.py` flagging slow first words, long turns,
  repeated or failed lookups, unspoken text, missing cards and errors.
- **Current information:** a road-closure answer came from last year's article and was told
  as current, with a weekday that did not match the date. Rule: for things happening now or
  soon, use only visibly current sources, say when unsure, and check weekdays against the
  date. mcp-staan 0.1.2 adds a `let_op` to results whose weekday and date don't fit this year,
  and marks results without a date.
- When the model wrote text next to a screen call and also filled `zeg`, both were spoken;
  now `zeg` goes first and the text is dropped once the answer has been spoken.

## Road questions from official data, parallel calls (0.4.38)

- A road answer from a news-aggregator page lost the end points: a direction ("toerit X ri.
  Y") became "the road between X and Y is closed", and an on-ramp closure on the route from
  home was missed. mcp-geo 0.1.3 adds `verkeer` (NDW open data along the car route from
  home); the rules send road questions there instead of to the web.
- The model is asked to put everything independent in the same round (weather and route,
  traffic and weather, two searches, the card catalog), which runs in parallel.
- Early answers (`zeg`, or the answer spoken before a card nudge) now go through `_speakable`
  too; markdown had reached TTS.

## Louder replies (0.4.39)

- The device's only output control (the media player volume) was already at max, while the
  TTS replies peaked around -9 dBFS. `audio.louder_flac` now peak-normalizes each FLAC reply
  to -1 dBFS and adds `tts_gain_db` (default 4) with a soft limiter above 0.6 of full scale:
  on a sample reply about 12 dB more average level with almost no limiting. FLAC is re-encoded
  16-bit with the sample count, so playback length is known instead of guessed.
- The end chime plays at 0.85 of full scale instead of 0.4.

## Speech shaped for a small speaker (0.4.40)

- The speaker is a 4 cm driver on the board's output. Before normalization, `audio.speaker_eq`
  now applies, zero-phase in the frequency domain, a 12 dB/octave high-pass at
  `tts_highpass_hz` (default 250) and a broad `tts_presence_db` boost around 3 kHz (default 4).
  The low end it cannot reproduce no longer eats headroom. On a sample reply the share below
  250 Hz dropped by about 4 dB and the 2–4 kHz share rose by about 4.5 dB, at the same overall
  level. 0 switches either off.

## Loudness actually applied (0.4.41)

- 0.4.39/0.4.40 left Voxtral replies unprocessed: Voxtral's FLAC is streamed without a sample
  count, libsndfile cannot read it (not even block-wise: its seek fails), and `louder_flac`
  fell back to the original by design. The unit test used a normal FLAC and missed it. With
  Voxtral the agent now asks for raw float32 PCM and encodes the processed FLAC itself
  (`audio.pcm_to_loud_flac`); other engines' FLAC still goes through `louder_flac`. On a sample
  reply: +13.7 dB average level at `tts_gain_db` 8.

## Even loudness over time (0.4.42)

- Long replies faded: Voxtral's voice on a 32 s poem went from about -13 to -30 dBFS, and one
  gain for the whole clip kept the end quiet. `audio.level` (option `tts_level`, on) now moves
  the gain per ~0.4 s window towards -18 dBFS (at most +15/-6 dB), gives silence the typical
  gain instead of boosting it, and smooths the gain over 0.25 s. The peak normalization uses the
  99.7th percentile, so a few lifted transients no longer hold the reply down; the soft limiter
  catches them. On the poem at `tts_gain_db` 8: about -7 dBFS at the start, -10 at the end
  (before: -9 and -29).
