# Step 06 — Grounding: live home context (HA exposed entities)

**Date:** 2026-06-07
**Goal:** stop the model guessing. With only fuzzy search results it invented devices,
miscounted and fabricated rooms.

## What was built — `home_context.py` (`HomeContext`)

A cached, room-grouped list of the home's devices, injected into the prompt so the model can
only name things that exist. Refreshed at startup, every `home_context_refresh_sec` (900 s)
and on demand (`POST /api/home/refresh`, the UI's refresh button).

Built from HA data:
- **Which entities:** HA's Assist-exposed set (`ha_get_entity_exposure`), not a hardcoded
  domain list; exposure curated in HA is the source of truth.
- **Names:** `ha_get_state` over the exposed ids.
- **Room:** entity → device → `area_id` (`ha_get_device` with `detail_level: "full"`,
  paginated) → area name (`ha_list_floors_areas`). Step 12 corrected this to prefer the
  entity's own area.

Each line looks like `Lamp keuken [light.lamp_keuken] (lamp)`, grouped by room. The
**entity_id is included** so the model calls tools directly: no search round, no guessed id.
`McpManager.call_raw(server, tool, args)` lets HomeContext use tools that are not in the
agent's `enabled_tools`.

## Prompt and sampling

- The list is the complete truth: never invent a device, count or room; use the bracketed
  entity_id; always use a tool for live state.
- `llm_temperature = 0.1`: at the default temperature mistral-small fabricated even with
  correct context.

## Results (mistral-small + 4 HA tools)

| Query | Before | After |
|---|---|---|
| switch a lamp off | 3 rounds, ~2.5 s (search → call → reply) | 2 rounds, 1.7 s (direct call) |
| temperature in a room | wrong id → 404 → search, 3–4 rounds | 2 rounds, 1.1 s |
| ask for a device that does not exist | invented one | answers from the list only |

## Decisions

- **Admin HA token** for the exposure list and the area registry; the agent limits itself via
  `enabled_tools`. (Step 09 later moved to the role-scoped Supervisor token.)
- **Open security item:** `ha_call_service` can call any service with an admin token, and
  `enabled_tools` does not limit the domain. Recommended: a service-domain allowlist (see
  [security](../reference/security.md)).
- Conversation memory (step 05) plus this grounding fixed both the lost-context and the
  hallucination classes.
