# reSpeaker Agent — add-on

Self-owned voice agent for the reSpeaker XVF3800: wake → STT → LLM (with tools from your
MCP add-ons) → TTS, plus a live trace UI. Talks to the device directly over the ESPHome
native API. LLM/STT/TTS are OpenAI-compatible (any compatible provider, hosted or local).

## Install

1. Install the MCP add-ons you want (e.g. `mcp-homeassistant`, `mcp-funbox`,
   `mcp-websearch`, `mcp-screen`) and start them.
2. Install this add-on. In **Configuration**, set:
   - **`api_key`** — your OpenAI-compatible provider's API key.
   - **`device_host`** — the reSpeaker's hostname/IP (default
     `respeaker-xvf3800-assistant.local`).
   - **`voice_enabled`** — start the voice pipeline on boot.
3. Start the add-on and open it from the sidebar.

The agent connects to the MCP add-ons over HTTP and **auto-discovers** their hostnames
via the Supervisor — you don't configure urls. Per-server tool curation + endpoints are
editable in the agent's UI (persisted to `/data/config.json`).

## Networking

- Runs on `host_network` so it reaches the reSpeaker by mDNS (ESPHome API `:6053`) and
  serves the TTS audio the device fetches for playback (`:8731`).
- The UI is served through **HA ingress** (authenticated by Home Assistant). The app
  rejects any request that isn't from the ingress proxy, so the `:8099` port carries no
  unauthenticated access even though it's bound on the host.

## Notes

- Only one client can be the device's voice handler. If Home Assistant currently owns the
  reSpeaker's assist pipeline, stop that before enabling voice here.
- Secrets stay in the add-on options / environment — never written to `config.json`.
