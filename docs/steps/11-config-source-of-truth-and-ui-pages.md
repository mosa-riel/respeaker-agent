# Step 11 — Add-on config as source of truth + paged web UI

How-to log (append-only). Two changes that go together: centralise the scalar
settings in the HA add-on config, and split the single-scroll web UI into pages.

## Config: one source of truth

All **scalar** settings now live in the add-on Configuration tab and are the source
of truth — `addon-run.py` writes every one into `/data/config.json` **every boot**
(`SCALAR_OPTS`). So you edit them in *Settings → Add-ons → reSpeaker Agent →
Configuration*, restart, done.

The web UI keeps only what HA's static schema form can't do well — these stay
editable in the agent and survive restarts (NOT in `SCALAR_OPTS`):

- **Voice** — live, refreshable dropdown from `GET /api/voices` (HA config can't fetch
  a remote list).
- **System prompt** — long multiline text.
- **STT extra params** — JSON object.
- **MCP servers + per-tool enable/disable** — nested list, managed by `/api/mcp`.

Everything else (`device_host`, models, urls, rates, VAD, toggles, `device_volume`,
`audio_sink`, `bluetooth_control`, …) shows **read-only** on the Settings page with a
banner pointing to the add-on config. `config.yaml` gained `options` + `schema` entries
(typed: `int(min,max)`, `float(0,1)`, `list(voxtral|openai)`, etc.) for all of them.

## Web UI: sidebar + pages

The 656-line single scroll became a left **sidebar nav** with pages (vanilla JS, no
build). Nav labels are plain text in the heading font (Poppins 500, no emoji/gradient):

| Page | Contents |
|---|---|
| Home | rollout strip · live voice-pipeline flow-graph |
| Live | trace monitor (prompts/transcripts/tool calls/errors) |
| Playground | test-chat (manual prompt → agent loop) |
| Bluetooth | search / pair / connect speakers (only when `bluetooth_control` on) |
| Instellingen | editable voice/prompt/stt_extra + read-only scalar mirror + device status |
| MCP servers | server list + per-tool toggles + add-by-URL |

Routing: `navigate(page)` toggles `.page.active` + `.nav-item.active`, syncs
`location.hash` (deep-linkable). The **per-stage settings modal was removed** — clicking
a flow-graph node now opens the Settings page. `drawWires()` re-runs when Home becomes
visible (flowmap needs a live size).

### Bluetooth page + REST API

`bluetooth.py` ops were lifted to module-level reusable coroutines (`bt_list`, `bt_scan`,
`bt_connect`, `bt_disconnect`) shared by the agent tools AND new endpoints:
`GET /api/bluetooth` (list + active sink + `enabled` gate), `POST /api/bluetooth/{scan,
connect,disconnect}`. All gated on `bluetooth_control` (403 otherwise; GET returns
`{enabled:false}` so the UI hides the nav item). Same security fence as the tools (fixed
binary, no shell, MAC-validated). The page shows the active sink, a Zoeken (scan) button,
and per-device Verbinden/Verbreken; a successful connect sets `audio_sink` and the page
reloads config.

## Files

- `respeaker_agent_addon/config.yaml` — full `options` + `schema` (all scalars)
- `addon-run.py` — `SCALAR_OPTS` enforced every boot
- `static/index.html` — sidebar + `.page` wrappers; Settings page; routing JS; modal removed
- `static/app.css` — `.app-shell/.sidebar/.nav-item/.page/.ro-grid` (+ ≤760px responsive)
- `web.py` — `GET /api/voices` (added step 10.5) powers the dropdown

## Verify

`uv run respeaker-agent` → `GET /` serves the paged UI; `/api/config` carries the new
keys; `/api/voices` returns voices on a valid key, `{voices:[],error:…}` otherwise (UI
falls back to manual). Smoke-tested locally: 4 nav items, CSS loaded, graceful no-key.
