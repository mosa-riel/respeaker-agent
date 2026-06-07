# Step 05 — Control-command latency tuning + two fixes

**Date:** 2026-06-07
**Goal:** Make device commands ("zet de keukenkopjes uit", "rolluik voordeur
omlaag") fast (<3s) and reliable. Plus fix a recurring SSE crash and explain a
config-save footgun.

## Latency: the tool count was the killer

Measured "zet de keukenkopjes uit" end-to-end through `/api/run`:

| Config | Latency | Acted? |
|---|---|---|
| mistral-medium + **80 HA tools** | **18–28 s** | sometimes failed to act |
| mistral-medium + 4 tools | 2.8–7 s | yes |
| **mistral-small + 4 tools** | **1.6–2.5 s** | yes, every time |

Two changes, both verified against real HA:
- **Curate tools per server** — `McpServer.enabled_tools` (empty = all; non-empty =
  only those). HA scoped to `ha_search_entities, ha_get_state, ha_call_service,
  ha_bulk_control`. This is the dominant latency lever and also improves tool
  selection. It is **curation, not security** (the non-admin token is security).
- **`mistral-small-latest`** is the default model — with a small tool set it's
  reliable AND ~2× faster than medium. (Earlier note "small hallucinates" was with
  the large HA tool set; few tools fixes it.)

`allow_tools`/`disabled_tools` from step 04 were folded into a single
`enabled_tools` allowlist — cleaner config (4 names, not 76) and a 1:1 match for
the UI's per-tool checkboxes (checked = enabled; all checked → `[]` = all).

Reliability nit still open: "keukenkopjes aan" occasionally targets
`script.kopjes_aan` instead of the light (search returns both). Canonical-entity
injection (task #8) fixes this and removes the search round (faster still).

## Fix 1 — SSE stream crashed every 15s (`StopAsyncIteration`)

The step-04 `wait_for(agen.__anext__(), 15)` reload fix had a bug: a timeout
*cancels* `__anext__`, which cancels the `q.get()` inside `bus.stream()`, which
runs the generator's `finally` and **ends the generator**. The next poll calls
`__anext__` on a dead generator → `StopAsyncIteration` → PEP-479 RuntimeError.
Fix: `TraceBus.subscribe()/unsubscribe()` and poll the queue directly — cancelling
`queue.get()` on timeout leaves the subscription intact. Verified: repeated
timeouts then deliveries work; clean unsubscribe.

## Footgun — editing config.json under a running server

`PUT /api/config` (frontend **Save**) calls `settings.save()`, which persists the
server's **in-memory** `mcp_servers`. If you hand-edit `config.json` while the agent
is running, the next Save overwrites your edit with stale in-memory state (also
re-serialises defaults like `env: {}`). Edit `config.json` only while stopped, or
use the UI (`/api/mcp`). The server is the source of truth at runtime.

## Verified

- mistral-small + 4 tools: "zet de keukenkopjes uit/aan", "doe keukenkopjes uit",
  "keukenkopjes aan" — all 1.6–2.5 s, all fired `call_service`, correct Dutch.
- `enabled_tools` save round-trip stable; SSE subscription survives timeouts.

## Open / next

- Task #8 — inject cached home context (entities) → skip the search round, fix the
  script-vs-light ambiguity, cut latency further. (ha_get_overview is verbose;
  prefer the exposed-to-Assist entity set.)
- Voice pipeline (#5): note voice adds STT + TTS (~0.8s TTFB) on top of the ~2s
  agent loop — budget ~3–4s to first audio.
</content>
</invoke>
