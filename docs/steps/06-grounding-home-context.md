# Step 06 — Grounding: live home context (HA exposed entities)

**Date:** 2026-06-07
**Goal:** Stop the model guessing. It invented devices ("plafondlicht"), miscounted,
and fabricated areas because it had no ground truth — only fuzzy search results.

## What was built — `home_context.py` (`HomeContext`)

Caches a room-grouped list of the home's devices, injected into the system prompt so
the model can only name things that exist. Refreshed at startup, periodically
(`home_context_refresh_sec`, default 900s), and on demand (`POST /api/home/refresh`,
the future "vernieuw apparaten" voice command).

Built by joining real HA data (admin token — see below):
- **Which entities** = HA's Assist-exposed set (`ha_get_entity_exposure`). NOT a
  hardcoded domain list — the user curates exposure in HA, that's the source of
  truth. Domain is derived from each entity_id; `DOMAIN_TYPE` only prettifies labels.
- **Friendly names** = `ha_get_state` over the exposed ids.
- **Room** = entity → device → `area_id` (`ha_get_device` with `detail_level:"full"`,
  paginated — the default omits the `entities` list) → area name
  (`ha_list_floors_areas`).

Each line: `Keukenkopjes [light.keukenkopjes] (lamp)` grouped by room. The
**entity_id is included** so the model calls tools directly — no search round, no
guessing a (wrong) id.

`McpManager.call_raw(server, tool, args)` lets HomeContext read these tools even when
they're not in the agent's curated `enabled_tools` set.

## Prompt + sampling

- System prompt: the list is the COMPLETE, ONLY truth; never invent a device/count/
  area; use the bracketed entity_id directly in tools; always a tool for live state.
- `llm_temperature = 0.1` — at the default temperature mistral-small fabricated even
  with correct context. Low temp ≈ no fabrication.

## Results (real HA, mistral-small + 4 HA tools)

| Query | Before | After |
|---|---|---|
| "zet de keukenkopjes uit" | 3 rounds, ~2.5s (search→call→reply) | **2 rounds, 1.7s** (direct call_service) |
| "badkamer temperatuur" | guessed wrong id → 404 → search → 3–4 rounds | **2 rounds, 1.1s**, direct get_state, honest "niet beschikbaar" |
| "heb ik een plafondlicht?" | invented one | grounded — only answers from the list |

## Model note

mistral-small + grounded context + entity_ids + temp 0.1 = fast (1–2s) and reliable
for both control and facts. medium grounds facts a touch better but is ~2× slower on
control; small is the better voice-agent default now that context is solid.

## Decisions / open

- **Admin HA token** (user's choice): needed for `ha_get_entity_exposure` + the area
  registry. We block at our layer via `enabled_tools` (4 HA tools).
- **OPEN security item:** `ha_call_service` is universal — with an admin token it can
  call ANY service (incl. `homeassistant.restart`). `enabled_tools` doesn't constrain
  the service domain. Recommended next: a service-domain allowlist guard on the
  call_service path (allow light/cover/climate/switch/fan/scene/script/media_player/
  lock; block homeassistant.*/recorder.*/hassio.*).
- Conversation memory (step 05 update) + this grounding together fixed the "ja →
  greeting" and the hallucination classes.
</content>
</invoke>
