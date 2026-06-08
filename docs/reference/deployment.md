# Reference — Deployment (Home Assistant add-ons)

> Living doc. The agent + each MCP run as **Home Assistant OS add-ons** on the
> always-on HA host (`192.168.18.31`, Supervisor present). Each MCP lives in its **own
> repo** and speaks **streamable-HTTP**; the agent connects by `url`. Bring-up uses
> **local `/addons`**; a GitLab add-on repository comes later.

## Topology

```
HA OS host — Supervisor add-ons
  respeaker-agent     [host_network] ingress UI :8099 · TTS audio host :8731 (LAN)
        │ connects by url ▼ (HA internal DNS)
  mcp-homeassistant   [bridged]  http://local-mcp-homeassistant:8086/mcp   (ha-mcp + SUPERVISOR_TOKEN)
  mcp-funbox          [bridged]  http://local-mcp-funbox:8785/mcp
  mcp-websearch       [bridged]  http://local-mcp-websearch:8786/mcp
  mcp-screen          [bridged]  http://local-mcp-screen:8788/mcp  + PNG host :8799 (LAN, ports)
LAN devices: reSpeaker .151 (fetches TTS :8731) · reTerminal .152 (fetches PNG :8799)
```

## Repos

| repo | role | add-on network | ports |
|---|---|---|---|
| `respeaker-agent` | the agent (this repo) | `host_network` | ingress 8099, TTS 8731 |
| `mcp-homeassistant` | HA control (wraps upstream `ha-mcp`) | bridged | 8086 (internal) |
| `mcp-funbox` | demo tools | bridged | 8785 (internal) |
| `mcp-websearch` | DuckDuckGo search | bridged | 8786 (internal) |
| `mcp-screen` | reTerminal e-paper | bridged | 8788 (internal), 8799 (LAN) |

Add-on files per repo: `config.yaml` (manifest), `Dockerfile`, source, `README.md`.

## Networking rationale

- **Agent = `host_network`.** It connects to the reSpeaker by mDNS over the ESPHome
  native API (`:6053`), and the TTS audio host (`tts_server.py`) auto-detects the host's
  real LAN IP so the device can fetch playback from `:8731` — both need the host stack.
- **MCPs = bridged**, reachable by HA internal DNS `local-mcp-<name>:<port>`. A
  host_network add-on (the agent) **can** reach bridged add-ons by name. Bridged gives
  isolation and a stable readable hostname.
- **`mcp-screen` is bridged too** (uniform url) but publishes its PNG port to the host
  LAN via a `ports` mapping (the reTerminal fetches `/screen.png`). It reaches the device
  API **by IP** — bridged containers can't do mDNS — so set `reterminal_host` to the
  device's reserved IP, not `reterminal-e1001.local`.

> The `local-` DNS prefix is for **local `/addons`** installs. Installing from a git
> add-on repository changes the prefix (hashed repo id) — update the `config.json` urls
> then. Confirm the exact hostname on the add-on's Supervisor page.

## Config & secrets (agent add-on)

- `addon-run.py` is the entrypoint. It maps the `mistral_api_key` option →
  `LLM_API_KEY`/`STT_API_KEY`/`TTS_API_KEY` env (secrets only in env, never in
  `config.json`); seeds `/data/config.json` from the shipped deployment config on first
  boot; always forces the ingress bind (`web_host=0.0.0.0`, `web_port=8099`); sets
  `RESPEAKER_INGRESS=1` and `RESPEAKER_CONFIG=/data/config.json`.
- `/data` is the persistent volume — `config.json` there survives updates and is the
  runtime source of truth (UI edits persist).

## MCP registry

MCP topology lives in the agent's `config.json` `mcp_servers` (seeded into `/data`,
runtime-managed by `/api/mcp` + the UI). Each entry is `{name, url, enabled,
enabled_tools, tool_arg_overrides}`. Adding an MCP (incl. an external one) = add a url.
*(Future option: auto-discover MCP add-ons via the Supervisor `/addons` API instead of a
hand-listed registry.)*

## HA-MCP credential

`mcp-homeassistant`'s `run.py` prefers a pasted `ha_token` option, else falls back to
`SUPERVISOR_TOKEN` + the Supervisor core proxy (`http://supervisor/core`), enabled by
`homeassistant_api: true`. The supervisor-token path needs no managed secret and is
role-scoped (`hassio_role`). Leave the options blank to use it.

## Install (local /addons bring-up)

1. Copy each repo folder into the HA host's `/addons` dir (via the SSH or Samba add-on).
2. Settings → Add-ons → ⋮ → check / install each **Local add-on**.
3. Set the agent add-on's `mistral_api_key` option; start the MCP add-ons, then the agent.
4. Open the agent UI from the HA sidebar (ingress). `/api/health` (the rollout strip)
   shows each MCP `up` with its tool count.
5. Reflash the reTerminal `online_image` url → `http://<ha-host-ip>:8799/screen.png`.

## Verification

- Each MCP add-on: Supervisor shows it running; `tools/list` answers on its url.
- Agent UI (ingress): all MCPs `connected`; device link up; end-to-end voice
  ("okay nabu" → tool → TTS on the reSpeaker); screen push redraws the reTerminal.
