# reSpeaker Agent — add-on

Self-owned voice agent for the reSpeaker XVF3800: wake → STT → LLM (with tools from your
MCP add-ons) → TTS, plus a live trace UI and e-paper screen rendering. LLM/STT/TTS are
OpenAI-compatible (any compatible provider, hosted or local).

## Configuration

- **API key** — your OpenAI-compatible provider's API key (Mistral works out of the box).
- **reSpeaker IP** (`device_host`) — the device's IP, e.g. `192.168.18.151` (use the IP,
  not the `.local` name — this add-on is bridged and has no mDNS).
- **HA host IP** (`tts_audio_host`) — this Home Assistant host's LAN IP, e.g.
  `192.168.18.31`. The reSpeaker fetches spoken replies from here.
- **Enable voice** — take over the device's wake-word pipeline. Stop Home Assistant's own
  Assist for this device first (only one client may own voice).

LLM/STT/TTS base-urls, models, and the voice id are edited in the agent's own UI (open it
from the sidebar; click a pipeline node).

## Connecting MCP servers

Install the MCP add-ons you want (`mcp-homeassistant`, `mcp-funbox`, `mcp-websearch`,
`mcp-screen`) and start them. Then in the agent UI → **MCP servers** → **Add** each by URL,
using that add-on's **Hostnaam** (its Info tab) + port:

```
home-assistant  http://<hostnaam>:8086/mcp
funbox          http://<hostnaam>:8785/mcp
websearch       http://<hostnaam>:8786/mcp
screen          http://<hostnaam>:8788/mcp
```

**Restart this add-on after adding servers** — MCPs connect at startup. The rollout strip
shows each connected server green.

For Home Assistant, curate a lean tool set in its MCP card: `ha_search_entities,
ha_get_state, ha_call_service` (ha-mcp exposes ~80 tools; fewer = better tool-selection +
fewer rate-limit retries).

## Networking

- **Bridged** network, so the agent resolves the MCP add-ons by their internal hostnames
  (a host_network add-on can't). It reaches the reSpeaker by IP and serves TTS audio on
  `:8731` (published to the LAN) using the configured HA host IP.
- The UI is served through **HA ingress** (authenticated by Home Assistant); it is not
  exposed on the LAN.

## Notes

- Secrets stay in the add-on options / environment — never written to `config.json`.
- `show_html` (via mcp-screen) renders HTML/CSS to the 800×480 1-bit e-paper — use MDI
  icons / inline SVG, not emoji.
