# Step 09 — Home Assistant add-ons + MCP-per-repo split

Append-only how-to log. Goal: move the agent off the dev laptop onto the always-on
HA OS host as a **Home Assistant add-on**, and split each MCP into **its own repo +
add-on**, all connected over **HTTP** so adding an MCP (incl. external ones) is just a
url. See [reference/deployment.md](../reference/deployment.md) for the living doc.

## What changed

### MCPs are now standalone HTTP servers, each in its own repo
The three local `scripts/*_mcp.py` were **removed from this repo** and rewritten as
streamable-HTTP MCP servers in sibling repos (GitLab `development` group):

| repo | port (MCP) | extra | network |
|---|---|---|---|
| `mcp-funbox` | 8785 | — | bridged |
| `mcp-websearch` | 8786 | — | bridged |
| `mcp-screen` | 8788 | PNG host `:8799` (LAN, `ports`) | bridged |
| `mcp-homeassistant` | 8086 | wraps upstream `ha-mcp` image | bridged |

Each: `server.py` + `pyproject.toml` + `Dockerfile` + `config.yaml` (add-on manifest)
+ `README.md`. The server skeleton is identical — SDK FastMCP
(`from mcp.server.fastmcp import FastMCP`), `mcp.run(transport="streamable-http")`,
host/port from env. **Gotcha:** the SDK's DNS-rebinding guard (`TransportSecuritySettings`)
defaults to a localhost-only Host allowlist with **no glob** — bound to `0.0.0.0` it
421s every request. These are server↔server endpoints on a trusted internal network, so
we set `enable_dns_rebinding_protection=False`.

`ha-mcp` (homeassistant-ai/ha-mcp v7.6.0) already ships HTTP mode (`ha-mcp-web`, port
8086, path `/mcp`) and a prebuilt multi-arch image. The add-on builds `FROM` that image
and overrides the entrypoint with `run.py`, which maps `SUPERVISOR_TOKEN` →
`HOMEASSISTANT_TOKEN` (with a pasted-long-lived-token fallback) and points it at the
Supervisor core proxy `http://supervisor/core`.

### Agent connects by url, uniform & readable
`config.json` `mcp_servers` switched from `command/args` (stdio) to `url`:
`http://local-mcp-<name>:<port>/mcp` (HA internal DNS for bridged add-ons; `local-`
prefix for local `/addons` installs — will differ for a git-repo install).
`mcp_client.py` already supported HTTP; no transport change needed there.

### Bug fixed: MCP connections must each own their task
Switching to HTTP exposed a structured-concurrency bug: `McpManager` stashed
`streamablehttp_client` + `ClientSession` on one shared `AsyncExitStack` and unwound
them piecemeal. streamable-HTTP spawns an internal anyio task group whose cancel scope
must enter AND exit in the same task — a failed HTTP connect raised
`RuntimeError: Attempted to exit cancel scope in a different task` and **hung startup**.
Fix: each server now runs in its **own asyncio task** that owns the full `async with`
chain and stays open (`await self._shutdown.wait()`) until `stop()`. `start()` waits on
a per-server `ready` event so the tool registry is populated before the agent runs; one
bad/slow server can't kill the rest. `_safe()` unwraps `ExceptionGroup` to the leaf so a
down server shows e.g. `[Errno -2] Name or service not known`, not a TaskGroup wrapper.

### Agent add-on
- `Dockerfile` (`python:3.13-slim`, `pip install .`) + `config.yaml` + `addon-run.py`.
- `addon-run.py`: maps the `mistral_api_key` option → `LLM/STT/TTS_API_KEY` env (secrets
  stay in env, never in config.json); seeds `/data/config.json` from the shipped
  deployment config on first boot; **always** forces `web_host=0.0.0.0`, `web_port=8099`
  (the ingress bind); sets `RESPEAKER_INGRESS=1`.
- **Ingress** (`ingress: true`, `ingress_port: 8099`, `ingress_stream: true`): UI served
  through the HA frontend (authenticated, no LAN port). `web.py` gained an `/` route that
  injects a fetch/EventSource shim prefixing the `X-Ingress-Path` (zero edits to the
  ~20 existing absolute-path calls; SSE trace stream works through ingress). Because
  `host_network` also exposes `:8099` on the LAN, a guard middleware rejects any client
  that isn't the ingress proxy `172.30.32.2`.
- **`host_network: true`**: needed so the agent reaches the device by mDNS over `:6053`
  and the TTS audio host (`:8731`) auto-detects the host's real LAN IP (the device fetches
  playback from it).

### Rollout-status strip
New `GET /api/health` aggregates agent + device link + each MCP (`connected` + tool
count / error) into one object. A small strip in `static/index.html` renders one chip
per app (green/red), polled by the existing `refresh()`. This is the in-HA "are the apps
up" view, alongside Supervisor's own add-on page.

## Verified locally
- funbox / websearch / screen: `tools/list` + a tool call over streamable-HTTP. ✅
- screen: dual server — MCP `:8788` + PNG host `:8799` (404 before publish). ✅
- agent boot with the 3 MCPs on localhost: `/api/health` shows them connected with
  tool counts in ~4s. ✅
- agent boot with unreachable DNS-name urls: startup **completes** (~14s), all 4 MCPs
  down with clear errors, **0 cancel-scope errors**. ✅
- ingress `/` render: local-dev keeps absolute urls (shim no-op); ingress mode prefixes
  `app.css` + carries the ingress path in the shim. ✅

## TODO (HA-runtime, can't verify off-host)
1. Install the 4 MCP add-ons + agent add-on from `/addons` (SSH/Samba).
2. Confirm the internal DNS hostnames match `config.json` urls (Supervisor shows the
   host); adjust the `local-` prefix if needed.
3. Confirm `mcp-homeassistant` works via `SUPERVISOR_TOKEN` through the core proxy;
   else paste a long-lived token in its options.
4. Reflash the reTerminal `online_image` url → `http://<ha-host-ip>:8799/screen.png`
   and set `mcp-screen`'s `reterminal_host` to the device's reserved IP.
5. Stop HA's pipeline ownership of the reSpeaker so the agent owns voice.
6. Add the `call_service` domain allowlist in `mcp-homeassistant`.
