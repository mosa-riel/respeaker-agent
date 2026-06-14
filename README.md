# reSpeaker Agent

Self-owned voice agent for the **reSpeaker XVF3800 + XIAO ESP32S3**. Talks to the device
directly over the ESPHome native API, runs an LLM tool-calling loop fed by **multiple MCP
servers**, plays TTS on the device **or a host / Bluetooth speaker**, renders **designed
screens to a reTerminal E1001 e-paper**, and serves a **paged web UI** (live pipeline +
trace monitor, playground, Bluetooth, settings). Replaces the fragile nested
Home-Assistant chain.

LLM/STT/TTS are **OpenAI-compatible** — base URLs + model ids are config, so any
compatible provider (Mistral by default, self-hosted later) works.

## Architecture

Everything runs as **Home Assistant OS add-ons**, each in its own repo:

| add-on | role | network | ports |
|---|---|---|---|
| **respeaker-agent** (this) | voice loop + UI | bridged, ingress | ingress 8099, TTS 8731 (LAN) |
| mcp-homeassistant | HA control (`ha-mcp`) | bridged | 8086 |
| mcp-funbox | demo tools | bridged | 8785 |
| mcp-websearch | DuckDuckGo | bridged | 8786 |
| mcp-screen | reTerminal e-paper | bridged | 8788, 8799 (PNG, LAN) |

The agent is **bridged** (not host_network) so it can resolve the MCP add-ons by their
internal hostnames. It reaches the reSpeaker by **IP**, and serves TTS audio the device
fetches from the HA host's LAN IP. The web UI is via **HA ingress** (authenticated, no
LAN port).

## Install (Home Assistant OS)

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories** — add each (one per line,
   **no leading space** or git fails with `protocol ' https' is not supported`):
   ```
   https://github.com/mosa-riel/respeaker-agent
   https://github.com/mosa-riel/mcp-homeassistant
   https://github.com/mosa-riel/mcp-funbox
   https://github.com/mosa-riel/mcp-websearch
   https://github.com/mosa-riel/mcp-screen
   ```
2. **Install the MCP add-ons first**, then the agent. (First build of `mcp-screen` is
   slow — it pulls Chromium; `mcp-homeassistant` pulls the upstream image.)
3. Configure + start each (below), then open the agent from the HA sidebar.

> Add-on networking: a host_network add-on can't resolve Supervisor DNS — that's why the
> agent is bridged. Each git-repo add-on gets a **hashed hostname** (see its **Info →
> Hostnaam**), e.g. `7de0882b-mcp-homeassistant`.

## Configure

### reSpeaker Agent → Configuration
- **API key** — your OpenAI-compatible provider key (Mistral works out of the box).
- **reSpeaker IP** (`device_host`) — the device's IP, e.g. `192.168.18.151` (IP, not
  `.local` — bridged add-ons have no mDNS).
- **HA host IP** (`tts_audio_host`) — this Home Assistant host's LAN IP, e.g.
  `192.168.18.31`. The reSpeaker fetches spoken replies from here.
- **Enable voice** — take over the device's wake-word pipeline. Stop HA's own Assist for
  this device first (only one client may own voice).

The Configuration tab is the **source of truth for all scalar settings** — LLM/STT/TTS
base-urls + models, rates, VAD, volume, follow-up, plus `audio_sink` and
`bluetooth_control` (below). `addon-run.py` writes them into `/data/config.json` every
boot. The agent **UI** keeps only what HA's static form can't do well and persists those
itself: the **voice dropdown** (fetched from your key), `system_prompt`, `stt_extra`
(JSON), and MCP server/tool management.

### mcp-homeassistant → Configuration
- Leave **`ha_token`** blank and set **`ha_url`** to `http://supervisor/core` → it
  authenticates with the injected Supervisor token (no long-lived token needed). Paste a
  long-lived token only if the proxy path doesn't work for you.

### mcp-screen → Configuration
- **`reterminal_host`** — the reTerminal's IP (e.g. `192.168.18.152`).

## Connect the MCP servers (agent UI)

Open the agent → **MCP servers** panel → for each add-on, **Add** a server by URL using
that add-on's **Hostnaam** + port:
```
home-assistant  http://<hostnaam>:8086/mcp
funbox          http://<hostnaam>:8785/mcp
websearch       http://<hostnaam>:8786/mcp
screen          http://<hostnaam>:8788/mcp
```
**Restart the agent after adding/changing servers** — MCPs connect at startup. Use the
**↻ Herstart** button in the header (restarts the add-on via the Supervisor). The rollout
strip turns green per connected server. (Voice / prompt / `stt_extra` edits apply **live**
— no restart.)

### Curate Home Assistant tools (important)
`ha-mcp` exposes ~80 tools. In the `home-assistant` MCP card, enable a **lean control
set** so the model picks well and stays fast:
```
ha_search_entities, ha_get_state, ha_call_service
```
(Add `ha_get_todo`/`ha_set_todo_item`/`ha_remove_todo_item` for shopping-list control.)
`ha_call_service` is the on/off workhorse; for "lights in room X" the agent searches with
`area_filter` + `domain_filter` and calls it per entity.

## Audio output & Bluetooth

By default TTS plays on the reSpeaker's own speaker. Set **`audio_sink`** to play on a
host PulseAudio sink instead — a paired Bluetooth speaker
`bluez_sink.<MAC>.a2dp_sink`, or `"default"` for the host default. The mic stays the
reSpeaker. (Follow-up mic-reopen rides the device path, so it's disabled in local-sink
mode.)

Turn on **`bluetooth_control`** to manage speakers from the app. The **Bluetooth** page
(and the voice/chat tools) can **search · pair · connect · disconnect**; a successful
connect sets `audio_sink` automatically. The add-on already grants the host access
(`audio: true` → Supervisor PulseAudio, `host_dbus: true` → host BlueZ; image ships
`bluez` + `pulseaudio-utils`) — verified working **in the HA add-on (Docker)**. Unpair is
a host action: `bluetoothctl remove <MAC>`.

> HA OS itself can't be a Bluetooth A2DP sink — the agent's **host adapter** owns the
> speaker, not HA. See [voice-audio](docs/reference/voice-audio.md) + [steps/10–11](docs/steps/).

## Web UI (HA ingress)

Left sidebar, paged: **Home** (live voice-pipeline flow-graph) · **Live** (trace monitor)
· **Playground** (test chat → full agent loop) · **Bluetooth** (when enabled) ·
**Instellingen** (voice/prompt/stt_extra editable + read-only mirror of the add-on
scalars + device status) · **MCP servers**.

## reTerminal e-paper

Flash the device's `online_image` url to the HA host where `mcp-screen` runs
(`devices/reterminal-e1001/`):
```
url: "http://<HA-host-ip>:8799/screen.png"
uvx esphome run reterminal-e1001.yaml --device <reterminal-ip>   # OTA
```
Then the agent's `show_text` / `show_image` / `show_html` redraw the screen.
`show_html` renders full HTML/CSS (Chromium) to 800×480 1-bit — use **MDI icons / inline
SVG, not emoji**, high contrast.

## Run locally (dev)

```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
make run                        # http://127.0.0.1:8730
```
Run each MCP repo's `server.py` and point the `mcp_servers` urls at
`http://127.0.0.1:<port>/mcp`.

## Config & secrets
- **`config.json`** — non-secret settings (UI-editable). Gitignored.
- **`.env`** — secrets only (`RESPEAKER_NOISE_PSK`, `LLM_API_KEY`; STT/TTS fall back to
  `LLM_API_KEY`). Never written to disk by the app or returned by the API.

## More
[`CLAUDE.md`](CLAUDE.md), [`docs/PLAN.md`](docs/PLAN.md),
[deployment](docs/reference/deployment.md), [security](docs/reference/security.md).
