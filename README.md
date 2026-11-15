# reSpeaker Agent

Self-hosted voice agent for the **reSpeaker XVF3800 + XIAO ESP32S3**. It talks to the device
directly over the ESPHome native API, runs an LLM tool-calling loop fed by **multiple MCP
servers**, plays replies on the device or on a host / Bluetooth speaker, draws cards on a
**reTerminal E1001 e-paper** screen, and serves a paged web UI (live pipeline, trace monitor,
playground, Bluetooth, settings). It replaces a nested chain of Home Assistant integrations.

LLM, STT and TTS are **OpenAI-compatible**: base URLs and model ids are configuration, so any
compatible provider works (Mistral by default; a self-hosted server is a config change).

## Architecture

Everything runs as **Home Assistant OS add-ons**, each in its own repo:

| add-on | role | network | ports |
|---|---|---|---|
| **respeaker-agent** (this repo) | voice loop + UI | bridged, ingress | ingress 8099, TTS audio 8731 (LAN) |
| mcp-homeassistant | HA control (wraps `ha-mcp`) | bridged | 8086 |
| mcp-funbox | demo tools | bridged | 8785 |
| mcp-websearch | DuckDuckGo search | bridged | 8786 |
| mcp-screen | reTerminal e-paper | bridged | 8788, PNG 8799 (LAN) |

The agent is bridged so it can resolve the MCP add-ons by their internal hostnames. It reaches
the reSpeaker by IP and serves TTS audio that the device fetches from the HA host's LAN IP. The
web UI is served through HA ingress (authenticated, no LAN port). Details:
[deployment](docs/reference/deployment.md).

## Install (Home Assistant OS)

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**: add each URL, one per line, with
   **no leading space** (otherwise git fails with `protocol ' https' is not supported`):
   ```
   https://github.com/mosa-riel/respeaker-agent
   https://github.com/mosa-riel/mcp-homeassistant
   https://github.com/mosa-riel/mcp-funbox
   https://github.com/mosa-riel/mcp-websearch
   https://github.com/mosa-riel/mcp-screen
   ```
2. Install the MCP add-ons first, then the agent. The first build of `mcp-screen` is slow (it
   pulls Chromium); `mcp-homeassistant` pulls the upstream image.
3. Configure and start each (below), then open the agent from the HA sidebar.

## Configure

### reSpeaker Agent
- **API key** (`api_key`): your OpenAI-compatible provider key (Mistral works out of the box).
  The optional `llm_api_key` lets the LLM use another provider than STT/TTS.
- **reSpeaker IP** (`device_host`): the device's IP, e.g. `192.168.1.50`. Use the IP, not the
  `.local` name: bridged add-ons have no mDNS.
- **HA host IP** (`tts_audio_host`): this Home Assistant host's LAN IP, e.g. `192.168.1.10`.
  The reSpeaker fetches spoken replies from here.
- **Enable voice** (`voice_enabled`): take over the device's wake-word pipeline. Stop HA's own
  Assist pipeline for this device first; only one client can own voice.

The add-on Configuration tab is the **source of truth for all scalar settings** (endpoints,
models, rates, VAD, volume, follow-up, `audio_sink`, `bluetooth_control`, …); `addon-run.py`
writes them into `/data/config.json` on every boot. The agent UI keeps only what HA's static
form can't do well: the voice dropdown, `system_prompt`, `stt_extra` (JSON) and MCP
server/tool management.

### mcp-homeassistant
Leave `ha_token` blank and set `ha_url` to `http://supervisor/core`: it then authenticates
with the injected Supervisor token. Paste a long-lived token only if that path doesn't work.

### mcp-screen
Set `reterminal_host` to the reTerminal's IP.

## Connect the MCP servers

In the agent UI → **MCP servers**, **Add** each server by URL, using the add-on's hostname
(shown on its Info tab) and port:
```
home-assistant  http://<hostname>:8086/mcp
funbox          http://<hostname>:8785/mcp
websearch       http://<hostname>:8786/mcp
screen          http://<hostname>:8788/mcp
```
Any other streamable-HTTP MCP server is added the same way. **Restart the agent after adding
or removing servers** (the **Herstart** button in the header restarts the add-on via the
Supervisor); the status strip turns green per connected server. Voice, prompt and `stt_extra`
edits apply live.

### Curate the Home Assistant tools
`ha-mcp` exposes ~80 tools. Enable a lean control set in the `home-assistant` card so the
model picks well and stays fast:
```
ha_search_entities, ha_get_state, ha_call_service
```
Add `ha_get_todo` / `ha_set_todo_item` / `ha_remove_todo_item` for shopping-list control.
Tool curation is not access control; see [security](docs/reference/security.md).

## Audio output and Bluetooth

TTS plays on the reSpeaker's speaker by default. Set `audio_sink` to a host PulseAudio sink
(`bluez_sink.<MAC>.a2dp_sink` for a paired Bluetooth speaker, or `"default"`) to play there
instead; the mic stays the reSpeaker. With `bluetooth_control` on, the **Bluetooth** page and
the agent's tools can scan, pair, connect and disconnect speakers. HA OS itself cannot be an
A2DP sink, so the add-on drives the host's adapter. See
[voice & audio](docs/reference/voice-audio.md).

## Web UI (HA ingress)

Sidebar pages: **Home** (live pipeline flow-graph) · **Live** (trace monitor, recordings) ·
**Playground** (test chat through the full agent loop) · **Bluetooth** (when enabled) ·
**Instellingen** (editable voice/prompt/`stt_extra`, read-only mirror of the add-on settings,
device status) · **MCP servers**.

## reTerminal e-paper

The ESPHome config is in `devices/reterminal-e1001/`. Point its `online_image` url at the host
running `mcp-screen` and flash it:
```
url: "http://<ha-host-ip>:8799/screen.png"
uvx esphome run reterminal-e1001.yaml --device <reterminal-ip>   # OTA
```
The screen tools then redraw it. `show_html` renders HTML/CSS (Chromium) to 800×480 1-bit:
use MDI icons or inline SVG, not emoji, and high contrast.

## Run locally (dev)

```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
make run                        # http://127.0.0.1:8730
```
Run each MCP repo's `server.py` and point the `mcp_servers` urls at
`http://127.0.0.1:<port>/mcp`.

- **`config.json`**: non-secret settings, gitignored.
- **`.env`**: secrets only (`RESPEAKER_NOISE_PSK`, `LLM_API_KEY`; STT/TTS fall back to
  `LLM_API_KEY`). Never written to disk by the app or returned by the API.

## Docs

[Index](docs/README.md) · [plan](docs/PLAN.md) · [deployment](docs/reference/deployment.md) ·
[voice & audio](docs/reference/voice-audio.md) · [security](docs/reference/security.md)
