# Reference — Deployment (Home Assistant add-ons)

The agent and each MCP server run as **Home Assistant OS add-ons**, each in its own public
GitHub repo structured as an add-on repository. MCP servers speak **streamable HTTP**; the
agent connects to them by URL. Install steps and the port table are in the
[README](../../README.md).

## Add-on repositories and image

- Each repo has `repository.yaml` at the root and the add-on in a slug subfolder
  (`<slug>/config.yaml` + `Dockerfile` + source). The Supervisor clones the repo and builds
  the image locally; no registry or CI.
- The agent's build context (`respeaker_agent_addon/`) can't see the app at the repo root, so
  its Dockerfile **git-clones the repo**. An `ADD` of the latest-commit API URL busts the layer
  cache, so every new commit is cloned fresh instead of reusing stale code.
- The image installs `requirements.lock` (exported from `uv.lock` with `make lock`), then the
  app with `--no-deps`. A bare `pip install .` once resolved `mcp` to a new major version and
  no MCP server connected (step 12).
- The image includes `bluez` (`bluetoothctl`) and `pulseaudio-utils` (`paplay`).

## Networking

- **Agent: bridged.** A `host_network` add-on cannot resolve the Supervisor's internal DNS, so
  it could not reach the MCP add-ons by hostname. Consequences:
  - The reSpeaker is reached **by IP** (`device_host`, ESPHome API `:6053`); bridged
    containers have no mDNS.
  - The device fetches TTS audio from the agent, so `:8731` is published to the LAN
    (`ports`) and the URL uses `tts_audio_host`, the HA host's LAN IP.
  - The UI is served through **ingress** (`ingress_port: 8099`, `ingress_stream: true`) and
    is not on the LAN. With `RESPEAKER_INGRESS=1` a guard also rejects any client other than
    the ingress proxy `172.30.32.2` (defense in depth). A fetch/EventSource shim adds the
    `X-Ingress-Path` prefix, so the UI's absolute API paths and the SSE trace stream work
    through ingress.
- **MCP add-ons: bridged, internal only.** Git-repo add-ons get a hashed hostname prefix
  (e.g. `abc123-mcp-funbox`), shown on the add-on's Info tab; add each server in the agent UI
  as `http://<hostname>:<port>/mcp`.
- **`mcp-screen`** also publishes its PNG host (`:8799`) to the LAN, because the reTerminal
  fetches `/screen.png`. It reaches the device API by IP (`reterminal_host`).

## Add-on permissions (agent)

| manifest key | why |
|---|---|
| `ingress: true` | authenticated UI through the HA frontend |
| `hassio_api: true` | the UI's **Herstart** button (`POST /addons/self/restart`) |
| `audio: true` | maps the Supervisor PulseAudio server (`PULSE_SERVER`) so `paplay` reaches host sinks, incl. `bluez_sink.<MAC>.a2dp_sink` |
| `host_dbus: true` | lets the opt-in `bluetooth_control` tools drive the host BlueZ adapter |

A speaker can be paired once on the host (Terminal add-on: `bluetoothctl pair/trust/connect`)
or, with `bluetooth_control` on, from the agent's Bluetooth page or by voice. A successful
`bluetooth_connect` sets `audio_sink` in memory; set `audio_sink` in the add-on options to keep
it across restarts.

## Config and secrets

`addon-run.py` is the entrypoint. On every boot it:

- maps the **`api_key`** option to `STT_API_KEY`/`TTS_API_KEY`, and to `LLM_API_KEY` unless
  **`llm_api_key`** is set (so the LLM can sit at another provider). Keys live only in the
  environment, never in `config.json`;
- seeds `/data/config.json` from the shipped config on first boot;
- writes every scalar add-on option (`SCALAR_OPTS`) into `/data/config.json`, so the
  Configuration tab is the source of truth for them;
- forces the ingress bind (`web_host=0.0.0.0`, `web_port=8099`) and sets `RESPEAKER_INGRESS=1`.

`/data` is persistent: `config.json` survives updates and keeps the UI-owned settings
(`tts_voice_id`, `system_prompt`, `stt_extra`, `mcp_servers`). The turn log
(`turns.jsonl`) and recordings live there too, so they are part of the add-on's backups.

## MCP connections and reconnects (0.4.28)

Each MCP server runs in its own task with a reconnect loop (`McpManager._serve`). A server
that is not up at boot is retried (5 s, doubling to 60 s); a server that restarts later (for
example an add-on update) is reconnected. A tool call that fails with "Session not found" or
a broken transport triggers a reconnect and is retried once on the new session. Tools are
re-registered on every connect, so new tools from an updated MCP add-on appear without
restarting the agent; removed ones linger until a restart. Adding or removing a server still
needs a restart.

## Home Assistant MCP credential

`mcp-homeassistant`'s `run.py` prefers a pasted `ha_token` option; otherwise it uses
`SUPERVISOR_TOKEN` through the Supervisor core proxy (`http://supervisor/core`), enabled by
`homeassistant_api: true`. That path needs no managed secret and is role-scoped
(`hassio_role`). Leave the options blank to use it.

## Verification

- Each MCP add-on runs in the Supervisor and shows green in the agent's status strip
  (`GET /api/health`: device link plus each MCP `connected` with its tool count).
- End to end: wake word → tool call → reply on the speaker; a screen tool redraws the
  reTerminal.
