# Step 11 — Add-on config as source of truth + paged web UI

**Version:** 0.4.0
**Goal:** keep all scalar settings in the HA add-on configuration, and split the single-scroll
web UI into pages.

## Config: one source of truth

All **scalar** settings are add-on options; `addon-run.py` writes each one (`SCALAR_OPTS`)
into `/data/config.json` on **every boot**. Edit them under *Settings → Add-ons → reSpeaker
Agent → Configuration* and restart. `config.yaml` has typed `options` + `schema` entries
(`int(min,max)`, `float(0,1)`, `list(voxtral|openai)`, …).

The web UI keeps only what HA's static form can't do well; these are not in `SCALAR_OPTS` and
survive restarts:

- **Voice**: a live dropdown from `GET /api/voices` (HA config can't fetch a remote list).
- **System prompt**: long multiline text.
- **STT extra params**: a JSON object.
- **MCP servers and per-tool toggles**: a nested list, managed through `/api/mcp`.

Everything else is shown read-only on the settings page with a pointer to the add-on config.

## Web UI: sidebar and pages

Vanilla JS, no build. `navigate(page)` toggles the active page and syncs `location.hash`
(deep links). Clicking a flow-graph node opens the settings page (the per-stage modal was
removed).

| Page | Contents |
|---|---|
| Home | status strip, live pipeline flow-graph |
| Live | trace monitor (prompts, transcripts, tool calls, errors), recordings |
| Playground | test chat through the agent loop |
| Bluetooth | scan / connect / disconnect speakers (only with `bluetooth_control`) |
| Instellingen | voice, prompt, `stt_extra`; read-only add-on settings; device status |
| MCP servers | server list, per-tool toggles, add by URL |

## Bluetooth page and REST API

`bluetooth.py` operations became module-level coroutines (`bt_list`, `bt_scan`,
`bt_connect`, `bt_disconnect`) shared by the agent tools and new endpoints:
`GET /api/bluetooth` (devices, active sink, `enabled`) and
`POST /api/bluetooth/{scan,connect,disconnect}`. All are gated on `bluetooth_control` (403
when off; GET returns `{enabled:false}` so the UI hides the page) and share the tools'
security fence. A successful connect sets `audio_sink`.

## Verify

`uv run respeaker-agent` → `GET /` serves the paged UI; `/api/config` carries the new keys;
`/api/voices` returns voices with a valid key and `{voices:[], error:…}` otherwise (the UI
falls back to a manual id).
