# Step 09 — Home Assistant add-ons + one repo per MCP

**Versions:** 0.1.x – 0.2.x
**Goal:** move the agent from a dev machine to the always-on HA OS host as an add-on, and
split each MCP server into its own repo and add-on, connected over HTTP, so adding an MCP
(including external ones) is just a URL. Living doc: [deployment](../reference/deployment.md).

## MCP servers: standalone, streamable HTTP, one repo each

The local `scripts/*_mcp.py` servers moved out of this repo and became streamable-HTTP
servers in their own repos:

| repo | MCP port | extra |
|---|---|---|
| `mcp-funbox` | 8785 | |
| `mcp-websearch` | 8786 | |
| `mcp-screen` | 8788 | PNG host `:8799` on the LAN |
| `mcp-homeassistant` | 8086 | wraps the upstream `ha-mcp` image |

- Each is `server.py` + `pyproject.toml` + `Dockerfile` + `config.yaml`, using the SDK's
  FastMCP with `mcp.run(transport="streamable-http")`.
- **Gotcha:** the SDK's DNS-rebinding guard defaults to a localhost-only Host allowlist with
  no glob; bound to `0.0.0.0` it answers 421 to everything. These are server-to-server
  endpoints on an internal network, so `enable_dns_rebinding_protection=False`.
- `ha-mcp` already ships an HTTP mode (port 8086, path `/mcp`) and a multi-arch image. The
  add-on builds `FROM` it and its `run.py` maps `SUPERVISOR_TOKEN` → `HOMEASSISTANT_TOKEN`
  against the core proxy `http://supervisor/core`, with a pasted-token fallback.
- The agent's `mcp_servers` switched from `command/args` (stdio) to `url`.

## Bug: each MCP connection must own its task

With HTTP, `McpManager` kept every `streamablehttp_client` + `ClientSession` on one shared
`AsyncExitStack`. streamable HTTP starts an anyio task group whose cancel scope must be
entered and exited in the same task, so a failed connect raised
`RuntimeError: Attempted to exit cancel scope in a different task` and **hung startup**.
Fix: each server runs in its own asyncio task that owns the whole `async with` chain until
`stop()`; `start()` waits on a per-server `ready` event; `_safe()` unwraps `ExceptionGroup`
so a down server shows a readable error. One bad server can no longer block the rest.

## Agent add-on

- `Dockerfile` + `config.yaml` + `addon-run.py`. `addon-run.py` maps the API key option to
  `LLM/STT/TTS_API_KEY` (never into `config.json`), seeds `/data/config.json`, forces the
  ingress bind (`web_host=0.0.0.0`, `web_port=8099`) and sets `RESPEAKER_INGRESS=1`.
- **Ingress** (`ingress: true`, `ingress_port: 8099`, `ingress_stream: true`): the UI goes
  through the HA frontend. The `/` route injects a fetch/EventSource shim that prefixes
  `X-Ingress-Path`, so the existing absolute API paths and the SSE stream work unchanged.
  A guard rejects clients other than the ingress proxy `172.30.32.2`.
- **Status strip:** `GET /api/health` aggregates agent, device link and each MCP
  (`connected`, tool count or error); the UI shows one chip per component.
- The Dockerfile clones the repo (the build context can't see the repo root) and busts the
  layer cache on every new commit (0.2.4); before that, add-on updates kept running stale
  code.

## Networking: host network → bridged

The first add-on used `host_network` (device by mDNS, TTS host IP auto-detected) and
discovered the MCP add-ons' hashed hostnames through the Supervisor API. But a host_network
add-on cannot resolve the Supervisor's internal DNS. 0.2.0 moved the agent to the **bridged**
network: MCP add-ons resolve by hostname, the device is reached by IP (`device_host`), TTS
audio is published on `:8731` with the host IP set in `tts_audio_host`, and the UI is
ingress-only. 0.2.2 dropped the auto-discovery: MCP servers are added by URL in the UI.

## Verified

- funbox / websearch / screen: `tools/list` and a tool call over streamable HTTP.
- Startup with unreachable MCP URLs completes, with clear per-server errors and no
  cancel-scope errors.
- Ingress rendering: local dev keeps absolute URLs; under ingress the shim prefixes them.
