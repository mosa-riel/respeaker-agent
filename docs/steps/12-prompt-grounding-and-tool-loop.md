# Step 12 — Device list back in the prompt + tool-loop fixes

How-to log (append-only). A review of live traces against the code (2026-10-30) found
why device turns took 2–3 LLM rounds and why answers sometimes did odd things.

## What was wrong

- **No device list in the prompt.** Step "lean prompt" (3ca3cd9) replaced the
  HomeContext list with a per-MCP tool-name summary, so every device turn did
  search → act/read → answer. HomeContext kept refreshing every 15 min for nothing,
  and the stored system prompt still pointed at "de lijst hieronder" that wasn't there.
  The trade-off was also off: the list is ~3.6k chars (~1k tokens); the tool specs
  sent on *every* call are ~36k chars, `ha_set_entity` alone 9k.
- **`llm_temperature` was never sent.** The setting existed (0.1, the step-06
  anti-fabrication fix) but `_chat` never put it in the request body, so Mistral's
  per-model default applied.
- **Tool calls ran in series.** "Alle lampen in de keuken" = N `ha_call_service`
  calls in one round, awaited one by one.
- **One conversation for every wake.** The firmware sends no conversation id, so all
  voice turns share `"voice"` with a 10-minute TTL: a new question minutes later still
  carried the previous session as history.
- **Wrong rooms in the device list.** HomeContext took each entity's room from its
  *device*, ignoring the entity's own area. One bridge device carries entities from
  several rooms: all Somfy RTS shutters landed in Hal (bedroom shutters included) and
  the ZHA group `light.keuken_kopjes` in Meterkast (the coordinator's area).
- **Markdown reached TTS** (`**21,1 graden**`).
- Dead code: `echo`/`get_time` demo tools (sent as specs on every call), `_compact`,
  `sources_summary`.

## Changes

- `agent.py`: prompt = stored system prompt + `Het is nu <dag datum tijd>` + runtime
  tool rules + `=== APPARATEN ===` (HomeContext list with entity_ids). Rules: act
  directly with the listed entity_id, no state check before or after a command,
  search only for what is not in the list. `temperature` is sent. Tool calls of one
  round run concurrently (`asyncio.gather`), results appended in call order.
  `_speakable()` strips `*`, backticks and heading markers from the reply.
- `home_context.py`: reads the entity registry (`ha_get_entity`, batches of 50); the
  entity's own `area_id` wins over the device's, and HA aliases are listed as
  `(ook: …)` so the model matches spoken names.
- `voice.py`: gets the HomeContext instead of the MCP manager; clears the
  conversation when a follow-up session ends (no reply → mic closes), so the next
  wake starts fresh. A reply keeps history for the follow-up.
- `web.py`: `/api/run` uses the same device list; registry starts empty (no demo tools).
- `tools.py`, `mcp_client.py`, `config.py`: demo tools, `sources_summary` and the
  duplicated area paragraph in the default prompt removed.

## Verify

Offline: fake LLM + fake tools — prompt contents, `temperature` in every request,
three 0.5 s tools finish in 0.5 s, tool messages keep call order, markdown stripped,
history cleared at session end and kept for a follow-up; HomeContext puts an entity
in its own area over its device's and lists aliases.

Live (read-only questions via `/api/run`, mistral-medium-latest, full 21-tool set):

| Question | Before (0.4.13) | After (0.4.16) |
|---|---|---|
| Is de lamp in de eetkamer aan? | 4.7 s, 2 rounds (search) | 5.0 s, 2 rounds (direct get_state) |
| Hoe warm is het in de badkamer? | 8.0 s, 3 rounds (search → get_state) | 5.3 s, 2 rounds |
| Welke lampen zijn er in de keuken? | 4.7 s, 2 rounds (search) | 2.7 s, 1 round (from the list) |
| Hoe laat is het? | 2 rounds (get_time) | 2.5 s, 1 round |
| Vertel een kort mopje | 2.5 s, 1 round | 2.6 s, 1 round |

The remaining time is mostly tool specs: the same questions with the trimmed tool set
(see Open) ran 3.5 s / 0.8 s for a state question / joke in an offline harness.

## Deploy incident (0.4.14 → 0.4.15 → 0.4.16)

0.4.14 rebuilt with a bare `pip install .`, which pulled mcp 2.3.0 (`mcp>=1.27.2` had
no upper bound): `streamablehttp_client` is gone in mcp 2, so no MCP server connected.
0.4.15: the Dockerfile installs `requirements.lock` (exported from `uv.lock`, `make
lock`), mcp capped below 2. 0.4.16: ported to mcp 2.x — `streamable_http_client`
yields `(read, write)`; result/tool fields are snake_case (`structured_content`,
`is_error`, `input_schema`). Works against ha-mcp 7.6.0 (the MCP add-on) and the
other MCP add-ons.

## Open (config, not code)

- Trim `enabled_tools` for home-assistant to what voice needs: `ha_set_entity`
  (registry edits: rename, area, disable, expose), `ha_get_entity`,
  `ha_config_list_groups`, `ha_config_get_label`, `ha_get_device` are admin tools,
  ~17k chars of specs per call, and `ha_set_entity` reads like "set state".
- `ha_call_service` is still unrestricted (step 06 open item): a service-domain
  allowlist on the call path.

## Follow-up (0.4.17)

- `llm_api_key` add-on option: the LLM can sit at another provider (hostyourai, Qwen3-235b)
  while `api_key` keeps serving STT/TTS (Voxtral at Mistral).
- HomeContext drops exposed entity ids that HA has no state for. Assist exposure outlives
  deleted entities; a `ha_call_service` on such a ghost waited 10 s for a state change
  (seen live: `light.keuken_plafondlamp` in "zet alle lichten beneden aan").
- The device list starts with the floors (`Begane grond (niveau 0): Eetkamer, Hal, …`), so
  "beneden"/"boven" map to real rooms instead of a guess.

## Qwen3 list arguments (0.4.18)

Live on Qwen3-235b (hostyourai): "Welke lampen staan er beneden aan?" took 4 rounds,
7.8 s. Qwen sent `entity_id='["light.a", "light.b"]'` (a JSON string); ha-mcp looked up
one entity with that name, failed, and Qwen fell back to one call per lamp.
`mcp_client._decode_json_args` now decodes such strings when the property's schema
allows an array or object.

Live numbers on Qwen (0.4.17, before this fix), same questions as above: eetkamer 2.9 s,
badkamer temperature 2.5 s, keuken list 2.5 s, joke 1.2 s, time 1.2 s.

## LED ring and session end (0.4.19)

- The ring showed "waiting for command" (led_beam) through the whole STT + LLM wait:
  after STT_VAD_END (ring: "thinking", breathe) `_handle_turn` sent STT_START again,
  which the firmware maps to on_listening. Removed; the ring now breathes until TTS_START
  (comet) — a visible "wait".
- Follow-up windows sometimes caught a click or the tail of the reply: ~1.5 s in, VAD
  saw "speech", STT came back empty, and the end chime was refused by the still-busy
  device (announce `success=False`, which was not checked) — no audible end. Now less
  than 250 ms of voiced audio before a pause is ignored as a blip, and a refused chime
  is retried once and traced.
