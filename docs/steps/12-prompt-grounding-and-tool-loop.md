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

Live (read-only questions via `/api/run`, before):

| Question | Before |
|---|---|
| Is de lamp in de eetkamer aan? | 4.7 s, 2 rounds (search) |
| Hoe warm is het in de badkamer? | 8.0 s, 3 rounds (search → get_state) |
| Welke lampen zijn er in de keuken? | 4.7 s, 2 rounds (search) |
| Hoe laat is het? | 2 rounds (get_time) |
| Vertel een kort mopje | 2.5 s, 1 round |

## Open (config, not code)

- Trim `enabled_tools` for home-assistant to what voice needs: `ha_set_entity`
  (registry edits: rename, area, disable, expose), `ha_get_entity`,
  `ha_config_list_groups`, `ha_config_get_label`, `ha_get_device` are admin tools,
  ~17k chars of specs per call, and `ha_set_entity` reads like "set state".
- `ha_call_service` is still unrestricted (step 06 open item): a service-domain
  allowlist on the call path.
