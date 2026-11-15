# Step 04 — MCP client layer, multi-server, HA tool scoping

**Date:** 2026-06-07
**Goal:** feed real tools into the agent from MCP servers (Home Assistant first), support
several servers and adding one from the UI, and keep HA's admin surface away from the voice
agent.

## What was built

- `mcp_client.py`: `McpManager` connects every enabled `mcp_servers` entry at startup (stdio
  via command/args, or remote HTTP/SSE via url), lists tools and registers them in the shared
  `ToolRegistry` as `{server}__{tool}`; dispatch routes back to the owning session. One bad
  server logs an error without stopping the rest.
- `config.py`: `McpServer` gained `env` (non-secret only), per-tool curation and a
  `transport` property.
- `web.py`: `GET /api/mcp` (servers, status, tool counts, errors), `POST /api/mcp` (add a
  **remote URL** server only), `PATCH /api/mcp/{name}` (enable/disable),
  `DELETE /api/mcp/{name}`. Changes persist to `config.json` and apply after a restart.
- UI: **MCP servers** card with status, tool count, per-tool toggles and an add form.
- HA credentials (`HOMEASSISTANT_URL`, `HOMEASSISTANT_TOKEN`) live in `.env`; the stdio
  subprocess inherits them.

## Decision: access control vs tool curation

ha-mcp exposes ~80 tools, including full admin (`ha_restart`, `ha_config_set_*`, add-on,
HACS, registry and backup tools, `ha_eval_template`, …). Two separate mechanisms:

- **Access control = the MCP server's own credentials.** A non-admin HA token makes HA reject
  admin, config, add-on, restart and registry calls server-side, so even a prompt-injected
  admin call fails at HA. This is not done in the agent.
- **Tool curation = per-tool toggles in the UI.** Hides tools to cut prompt size and bad tool
  choices. Explicitly not a security feature.

## Lessons

- With a non-admin token device control works, but HA's area/floor registry is admin-only, so
  room grouping via the registry is lost (revisited in step 06).
- `make dev` (uvicorn `--reload`) hung on shutdown: the SSE stream blocked forever in
  `q.get()`. It now wakes every 15 s (keepalive), and graceful shutdown is capped at 3 s
  (`--timeout-graceful-shutdown 3`, `timeout_graceful_shutdown=3` in `cli.py`).
