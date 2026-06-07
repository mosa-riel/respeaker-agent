# Step 04 — MCP client layer, multi-server, HA tool scoping

**Date:** 2026-06-07
**Goal:** Fan real tools into the agent from MCP servers (Home Assistant first),
support multiple servers + "add MCP" from the UI, and **scope HA to device control
only** (not its full admin surface).

## What was built

- `mcp_client.py` — `McpManager`: connects every enabled `mcp_servers` entry at
  startup (stdio via command/args, or remote http/sse via url), lists tools, and
  registers them into the shared `ToolRegistry` namespaced `{server}__{tool}`.
  Dispatch routes back to the owning `ClientSession.call_tool`. Contexts live in an
  `AsyncExitStack` entered/closed within the FastAPI lifespan (same task → satisfies
  anyio's cancellation rule). One bad server logs an error without killing the rest.
- `config.py` — `McpServer` gained `env` (non-secret only), `disabled_tools`
  (per-tool curation) and a `transport` property.
- `web.py` — wired the manager into lifespan; endpoints:
  `GET /api/mcp` (servers + live status + tool counts/errors),
  `POST /api/mcp` (add a **remote url** server — see security),
  `PATCH /api/mcp/{name}` (enable/disable), `DELETE /api/mcp/{name}` (remove).
  Add/remove/toggle persist to `config.json` and require a restart to (dis)connect.
- `static/` — **MCP servers** card: per-server status pill + tool count, enable
  toggle, remove, and an add-remote-server form.
- `.env` — `HOMEASSISTANT_URL` + `HOMEASSISTANT_TOKEN` (gitignored; token never in
  `config.json`). The stdio subprocess inherits them via `os.environ`.

## Access control vs tool curation (two separate things)

ha-mcp exposes **80 tools**, including full admin: `ha_restart`, `ha_reload_core`,
`ha_config_set_*`, `ha_*_addon`, `ha_hacs_*`, `ha_set/remove_entity/device`,
`ha_manage_backup`, `ha_eval_template`, … A voice assistant must not be able to use
these. We settled on a clear split (user's call):

- **Access control = the MCP server's own credentials.** The real boundary is a
  **non-admin Home Assistant user token** in `.env`. HA enforces it server-side:
  admin/config/add-on/restart/registry APIs are rejected for a non-admin token, so
  even a prompt-injected admin tool call fails at HA. This is NOT done in the agent.
- **Tool curation = `disabled_tools`, managed from the UI.** Default exposes
  everything the server offers; the MCP card lists every tool with an enable/disable
  checkbox (`disabled_tools` persisted to `config.json`, applied on restart). This
  is for cutting prompt bloat / hiding unwanted tools — explicitly *not* a security
  feature. (Earlier `allow_tools` default-deny filter removed in favour of this.)

## Verified (real HA)

- Catalog: 80 discovered/exposed; per-tool disable persists to `config.json`.
- **Admin token:** *"Welke ruimtes heb ik?"* → `ha_list_floors_areas` → real areas.
- **Non-admin token (final):** device path works — *"Staat er een lamp aan in de
  keuken?"* → `ha_search_entities` → correctly "Keukenkopjes staat aan"; lamp list
  with live states. **Caveat:** `ha_list_floors_areas` returns empty — HA's
  area/floor registry is **admin-only**. The agent works by entity name instead;
  room-grouping via the registry is lost under a non-admin token.

## Autoreload / shutdown fix

`make dev` (uvicorn `--reload`) hung on shutdown. Two causes fixed:
1. The SSE `/api/trace/stream` blocked forever in `q.get()` and never re-checked
   disconnect. Now wakes every 15s (keepalive) and is cancellable.
2. Added `--timeout-graceful-shutdown 3` (`make dev`) and
   `timeout_graceful_shutdown=3` (`cli.py`) so lingering connections are dropped.
**Note:** a server already running the *old* code can't reload itself to pick up
the fix — kill it once and `make dev` fresh; reloads work after that.

## Open / next

- "Add MCP" is remote-url only by design (security). A future server-side
  **allowlist of approved stdio launchers** could let the UI add vetted stdio
  servers (task #8/MCP backlog).
- Task #8: cache + periodically/voice-refresh the HA home context into
  `AgentLoop.run(context=)` (the device list, fetched via `ha_get_overview`).
- Voice pipeline (#5), pipeline-viz (#7), sidebar (#6).
</content>
</invoke>
