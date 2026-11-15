# Step 12 — Device list back in the prompt + tool-loop fixes

**Versions:** 0.4.14 – 0.4.19 · **Date:** 2026-10-30
**Goal:** a review of live traces against the code explained why device turns took 2–3 LLM
rounds and why answers sometimes went wrong.

## What was wrong

- **No device list in the prompt.** An earlier "lean prompt" change had replaced the
  HomeContext list with a per-MCP tool summary, so every device turn did search → act →
  answer, while the stored prompt still referred to a list that wasn't there. The trade-off
  was wrong: the list is ~1k tokens, while the tool specs sent on every call were ~36k chars
  (`ha_set_entity` alone 9k).
- **`llm_temperature` was never sent**, so the provider's default applied.
- **Tool calls ran in series**: "all lights in a room" = N calls awaited one by one.
- **One conversation for every wake.** The firmware sends no conversation id, so a new
  question minutes later still carried the previous session as history.
- **Wrong rooms.** HomeContext took each entity's room from its *device*. A bridge device
  carries entities from several rooms, so all shutters on one RTS bridge, and a Zigbee light
  group, landed in the bridge's or coordinator's room.
- **Markdown reached TTS** (`**21,1 graden**`).
- Dead code: demo tools sent as specs on every call, unused helpers.

## Changes

- `agent.py`: prompt = stored system prompt + runtime tool rules + `=== APPARATEN ===`
  (the HomeContext list with entity_ids). Rules: act directly on a listed entity_id, no state
  check before or after a command, search only for what is not listed. `temperature` is sent.
  Tool calls of one round run concurrently (`asyncio.gather`), results kept in call order.
  `_speakable()` strips markdown from the reply.
- `home_context.py`: reads the entity registry (`ha_get_entity`, batches of 50); the
  entity's own `area_id` wins over the device's; HA aliases are listed as `(ook: …)`.
- `voice.py`: clears the conversation when a follow-up session ends without a reply, so the
  next wake starts fresh (step 14 later kept the last three questions instead).
- `web.py`: `/api/run` uses the same device list; no demo tools.

## Results

Offline (fake LLM and tools): the prompt contents, `temperature` in every request, three
0.5 s tools finishing in 0.5 s, call order, markdown stripped, history handling, own-area
precedence and aliases.

Live, read-only questions via `/api/run` (mistral-medium-latest, 21 tools):

| Question | Before (0.4.13) | After (0.4.16) |
|---|---|---|
| Is a lamp in a room on? | 4.7 s, 2 rounds (search) | 5.0 s, 2 rounds (direct get_state) |
| Temperature in a room? | 8.0 s, 3 rounds | 5.3 s, 2 rounds |
| Which lamps are in a room? | 4.7 s, 2 rounds | 2.7 s, 1 round (from the list) |
| What time is it? | 2 rounds (tool) | 2.5 s, 1 round |

Most remaining time is tool specs: trim `enabled_tools` to what voice needs. Registry tools
(`ha_set_entity`, `ha_get_entity`, `ha_get_device`, group and label tools) are admin tools
with ~17k chars of specs per call, and `ha_set_entity` reads like "set state".

## Lesson: lock the image's dependencies (0.4.14 → 0.4.16)

0.4.14 built with a bare `pip install .`, which pulled mcp 2.x (`mcp>=1.27.2` had no upper
bound); `streamablehttp_client` is gone in mcp 2, so no MCP server connected. 0.4.15 installs
`requirements.lock` (exported from `uv.lock` by `make lock`) with mcp capped below 2; 0.4.16
ported to mcp 2.x (`streamable_http_client` yields `(read, write)`; snake_case fields such as
`structured_content`, `is_error`, `input_schema`).

## Follow-ups

- **0.4.17:** `llm_api_key` lets the LLM run at another OpenAI-compatible provider (e.g.
  Qwen3-235B) while `api_key` serves STT/TTS. HomeContext drops exposed entity ids without a
  state: Assist exposure outlives deleted entities, and a call on such a ghost waited 10 s.
  The device list starts with the floors, so "beneden"/"boven" map to real rooms.
- **0.4.18:** Qwen3 sent list arguments as a JSON string (`'["light.a", "light.b"]'`);
  ha-mcp treated it as one entity name and the model fell back to one call per lamp.
  `mcp_client._decode_json_args` decodes such strings when the schema allows an array or
  object.
- **0.4.19:** the LED ring showed "listening" during the whole STT + LLM wait because the
  turn sent `STT_START` again after `STT_VAD_END`; removed, so the ring breathes ("thinking")
  until `TTS_START`. Follow-up windows sometimes caught a click or the reply's tail and the
  end chime was refused by the busy device; under 250 ms of voiced audio is now ignored as a
  blip, and a refused chime is retried once.
