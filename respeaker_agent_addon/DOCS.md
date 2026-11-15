# reSpeaker Agent — add-on

Self-hosted voice agent for the reSpeaker XVF3800: wake → STT → LLM (with tools from your MCP
add-ons) → TTS, with a live trace UI and e-paper cards. LLM/STT/TTS are OpenAI-compatible
(any compatible provider, hosted or local).

## Configuration

- **API key** (`api_key`): your OpenAI-compatible provider's key (Mistral works out of the
  box). Set `llm_api_key` only if the LLM runs at another provider than STT/TTS.
- **reSpeaker IP** (`device_host`): the device's IP, e.g. `192.168.1.50`. Use the IP, not the
  `.local` name: this add-on is bridged and has no mDNS.
- **HA host IP** (`tts_audio_host`): this Home Assistant host's LAN IP, e.g. `192.168.1.10`.
  The reSpeaker fetches spoken replies from here.
- **Enable voice** (`voice_enabled`): take over the device's wake-word pipeline. Stop Home
  Assistant's own Assist pipeline for this device first; only one client can own voice.

All other scalar settings (endpoints, models, VAD, follow-up, audio sink, …) are on this
Configuration tab and are applied on every start. The voice, system prompt, `stt_extra` and MCP
servers are edited in the agent's own UI (open it from the sidebar).

## Connecting MCP servers

Install and start the MCP add-ons you want (`mcp-homeassistant`, `mcp-funbox`,
`mcp-websearch`, `mcp-screen`). In the agent UI → **MCP servers** → **Add** each by URL, using
that add-on's hostname (its Info tab) and port:

```
home-assistant  http://<hostname>:8086/mcp
funbox          http://<hostname>:8785/mcp
websearch       http://<hostname>:8786/mcp
screen          http://<hostname>:8788/mcp
```

**Restart this add-on after adding servers**; the status strip shows each connected server in
green. For Home Assistant, enable a lean tool set in its card: `ha_search_entities,
ha_get_state, ha_call_service` (ha-mcp exposes ~80 tools; fewer tools mean better tool
choice and fewer rate-limit retries).

## Networking

- **Bridged**, so the agent resolves the MCP add-ons by their internal hostnames. It reaches
  the reSpeaker by IP and serves TTS audio on `:8731` (published to the LAN) at the configured
  HA host IP.
- The UI is served through **HA ingress** (authenticated by Home Assistant) and is not exposed
  on the LAN.

## Notes

- Secrets stay in the add-on options / environment and are never written to `config.json`.
- `show_html` (mcp-screen) renders HTML/CSS to the 800×480 1-bit e-paper: use MDI icons or
  inline SVG, not emoji.
