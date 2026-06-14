# Reference — Deployment (Home Assistant add-ons)

> Living doc. The agent + each MCP run as **Home Assistant OS add-ons** (Supervisor on
> `192.168.18.31`). Each lives in its **own public GitHub repo**, structured as an HA
> **add-on repository**; the user adds the repo URLs in the Apps store. MCPs speak
> **streamable-HTTP**; the agent connects by `url` and auto-discovers their hostnames.

## Repos (public GitHub, `mosa-riel`)

| repo | role | add-on network | MCP port |
|---|---|---|---|
| `respeaker-agent` | the agent | `host_network` | ingress 8099, TTS 8731 |
| `mcp-homeassistant` | HA control (wraps upstream `ha-mcp`) | bridged | 8086 |
| `mcp-funbox` | demo tools | bridged | 8785 |
| `mcp-websearch` | DuckDuckGo search | bridged | 8786 |
| `mcp-screen` | reTerminal e-paper | bridged | 8788 (+ PNG 8799 on LAN) |

Each repo: `repository.yaml` at the root + the add-on in a slug subfolder
(`<slug>/config.yaml` + `Dockerfile` + source). Supervisor clones the repo and builds the
image locally — no registry/CI needed. The agent's add-on (`respeaker_agent_addon/`) has
a Dockerfile that **git-clones the repo** into the image, because the build context (the
subfolder) can't see the app at the repo root.

## Networking rationale

- **Agent = `host_network`.** It reaches the reSpeaker by mDNS over the ESPHome API
  (`:6053`), and the TTS audio host (`tts_server.py`) auto-detects the host's real LAN IP
  so the device can fetch playback from `:8731` — both need the host stack.
- **MCPs = bridged**, reachable from the host_network agent by their internal DNS
  hostname.
- **`mcp-screen`** is bridged but publishes its PNG port (`8799`) to the host LAN via a
  `ports` mapping (the reTerminal fetches `/screen.png`). It reaches the device API **by
  IP** — bridged containers can't do mDNS — so set `reterminal_host` to the device's
  reserved IP, not `reterminal-e1001.local`.

## Audio out + Bluetooth (step 10)

Optional: play TTS on a host speaker (incl. a Bluetooth A2DP speaker) instead of the
reSpeaker's own speaker. The agent add-on grants the host access for it:

- **`audio: true`** maps the Supervisor PulseAudio server in (`PULSE_SERVER`) so `paplay`
  reaches host sinks — incl. `bluez_sink.<MAC>.a2dp_sink` once a speaker is connected.
- **`host_dbus: true`** lets the optional `bluetooth_control` tools drive the host BlueZ
  adapter via `bluetoothctl`. Image installs `bluez` + `pulseaudio-utils`.
- Add-on options: **`audio_sink`** (sink name; empty = device speaker; `"default"` = host
  default) and **`bluetooth_control`** (expose the BT tools; off by default). Both
  enforced every boot by `addon-run.py`.

Pairing can be done once on the HA OS host (Terminal add-on: `bluetoothctl pair/trust/
connect`), or — with `bluetooth_control` on — by voice/UI via the agent. `audio_sink` is
auto-set on a successful `bluetooth_connect`; save config to persist across restart.

## MCP hostname auto-discovery

Git-repo add-ons get a **hashed DNS hostname prefix** (e.g. `abc123-mcp-funbox`), not
something predictable — so the `mcp_servers` urls can't be hardcoded. On boot,
`addon-run.py` (with `hassio_api: true`) queries `http://supervisor/addons`, matches the
installed MCP add-ons by slug suffix (`mcp_funbox`, `mcp_websearch`, `mcp_screen`,
`mcp_homeassistant`), and rewrites each `mcp_servers` url to
`http://<real-hostname>:<port>/mcp`. Best-effort; a no-op when run outside HA. Net:
**install an MCP add-on → the agent finds it; no url editing.**

## Config & secrets (agent add-on)

- `addon-run.py` is the entrypoint. It maps the **`api_key`** option →
  `LLM_API_KEY`/`STT_API_KEY`/`TTS_API_KEY` env (secrets only in env, never in
  `config.json`); seeds `/data/config.json` from the shipped config on first boot;
  auto-discovers MCP urls; always forces the ingress bind (`web_host=0.0.0.0`,
  `web_port=8099`); sets `RESPEAKER_INGRESS=1`.
- `/data` is the persistent volume — `config.json` there survives updates and is the
  runtime source of truth (UI edits persist). Endpoints/models are provider-agnostic
  (OpenAI-compatible base URLs in `config.json`).

## HA-MCP credential

`mcp-homeassistant`'s `run.py` prefers a pasted `ha_token` option, else falls back to
`SUPERVISOR_TOKEN` + the Supervisor core proxy (`http://supervisor/core`), enabled by
`homeassistant_api: true`. The supervisor-token path needs no managed secret and is
role-scoped (`hassio_role`). Leave the options blank to use it.

## Install

1. **Apps → ⋮ → Repositories**, add each URL (no leading space, or git fails with
   `protocol ' https' is not supported`):
   `https://github.com/mosa-riel/{respeaker-agent,mcp-funbox,mcp-websearch,mcp-screen,mcp-homeassistant}`
2. Install the 4 MCP add-ons (first build is slow — `mcp-screen` installs fonts,
   `mcp-homeassistant` pulls the upstream image). Start them.
   - `mcp-screen` → set `reterminal_host` to the device IP.
   - `mcp-homeassistant` → leave `ha_token`/`ha_url` blank (Supervisor token).
3. Install the agent add-on → set `api_key` → start.
4. Open the agent from the HA sidebar (ingress). `/api/health` (rollout strip) shows each
   MCP `up` with its tool count.
5. Reflash the reTerminal `online_image` url → `http://<ha-host-ip>:8799/screen.png`
   (config in `devices/reterminal-e1001/`).

## Verification

- Each MCP add-on: Supervisor shows it running; the agent's rollout strip shows it green.
- Agent UI (ingress): all MCPs `connected`; device link up; end-to-end voice
  ("okay nabu" → tool → TTS on the reSpeaker); screen push redraws the reTerminal.
