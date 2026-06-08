# reSpeaker Agent

Self-owned voice agent for the **reSpeaker XVF3800 + XIAO ESP32S3**. Talks to the device
directly over the ESPHome native API, runs an LLM tool-calling loop fed by **multiple MCP
servers**, renders custom screens to a **reTerminal E1001 e-paper**, and exposes a local
web UI with a live trace monitor. Replaces the fragile nested Home-Assistant integration
chain.

The LLM/STT/TTS endpoints are **OpenAI-compatible** — the base URLs and model ids are
config, so any compatible provider (hosted or self-hosted) works.

## Deploy as Home Assistant add-ons (recommended)

The agent and each MCP server are packaged as **HA add-ons**, each in its own repo.
Install on a Home Assistant OS host:

1. **Settings → Add-ons → ⋮ → Repositories**, add each (no leading spaces):
   ```
   https://github.com/mosa-riel/respeaker-agent
   https://github.com/mosa-riel/mcp-funbox
   https://github.com/mosa-riel/mcp-websearch
   https://github.com/mosa-riel/mcp-screen
   https://github.com/mosa-riel/mcp-homeassistant
   ```
2. Install the MCP add-ons, then this one. Set the agent's **`api_key`** (your
   OpenAI-compatible provider key) in its Configuration tab. Start it.
3. Open the agent from the HA sidebar (ingress). The rollout strip shows each MCP up.

The agent reaches the MCP add-ons over HTTP; it **auto-discovers** their hostnames via
the Supervisor API, so no urls need editing. See
[docs/reference/deployment.md](docs/reference/deployment.md).

## Run locally (dev)

```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
make run                        # http://127.0.0.1:8730  (or: uv run respeaker-agent)
```
For local dev, run each MCP repo's `server.py` and point the `mcp_servers` urls at
`http://127.0.0.1:<port>/mcp`.

## Config & secrets
- **`config.json`** — non-secret settings, editable from the UI (device host/port,
  LLM/STT/TTS base URLs + models, MCP servers). Gitignored.
- **`.env`** — secrets only (`RESPEAKER_NOISE_PSK`, `LLM_API_KEY`; STT/TTS keys fall
  back to `LLM_API_KEY`). Never written to disk by the app, never returned by the API/UI.

## Devices (ESPHome)

Flashing configs for both devices live in [`devices/`](devices/):
- `devices/respeaker-xvf3800/` — the voice satellite.
- `devices/reterminal-e1001/` — the e-paper screen (point its `online_image` url at the
  screen MCP's PNG host).

Copy each `secrets.yaml.example` → `secrets.yaml` (wifi + OTA), then
`uvx esphome run <device>.yaml`.

## More
Architecture, phases, and conventions: [`CLAUDE.md`](CLAUDE.md) and
[`docs/PLAN.md`](docs/PLAN.md). Security review: [`docs/reference/security.md`](docs/reference/security.md).
