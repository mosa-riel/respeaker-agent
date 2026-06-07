# CLAUDE.md — reSpeaker Agent

Knowledge for a fresh Claude session in this repo. Read `docs/PLAN.md` next.

## What this is

A self-owned **voice agent** for the **reSpeaker XVF3800 + XIAO ESP32S3**, replacing
the fragile nested Home-Assistant integration chain. It will own the device audio,
run an LLM tool-calling loop fed by **multiple MCP servers**, render custom screens
to a **reTerminal E1001 e-paper**, and expose a local web UI with a **live trace
monitor**. Endpoints are **OpenAI-compatible** (Mistral now, self-hostable later).

The hardware bring-up, the *working* HA fallback voice pipeline, and device flashing
live in the **sibling repo** `../ReSpeaker` (`/home/riel/Projects/ReSpeaker`). Read
its `docs/` for device/firmware/HA context — don't duplicate it here.

## Status

Phase 1 done: device link + web UI + live trace monitor (verified against the real
device). Phase 2 next: the voice flow. See `docs/PLAN.md` for phases + architecture.

## Run / commands

```bash
make sync     # uv sync (uv.lock committed)
make run      # http://127.0.0.1:8730
make dev      # autoreload
make sbom     # regenerate sbom.json (CycloneDX) — also at GET /api/sbom
```
Config: copy `config.example.json` → `config.json` (gitignored), `.env.example` →
`.env` (secrets). Python ≥3.11, managed with `uv`.

## Code map

- `src/respeaker_agent/config.py` — `Settings` (config.json, editable) vs `Secrets`
  (`.env`-only, never serialized or returned by the API).
- `device.py` — `DeviceLink`: resilient `aioesphomeapi` connection (ReconnectLogic).
- `trace.py` — `TraceBus`: the single structured event stream. **Every pipeline
  stage emits here** (`bus.emit(stage, text, direction=, data=, level=)`); stages:
  `device, wake, stt, llm-req, llm-rsp, tool, tts, info, error`.
- `web.py` — FastAPI: `/api/status`, `/api/trace` (+ `/stream` SSE), `/api/config`,
  `/api/sbom`. Static UI mounted at `/`.
- `static/{index.html,app.css}` — no-build UI; **styling lifted from
  `~/Projects/mosa.cloud.sources/commander`** (keep that look).

## Conventions (the user cares about these)

- **Strict docs every step:** for each milestone write/update both a `docs/steps/`
  how-to log (append-only) AND a `docs/reference/` living doc. Same turn as the work.
- **Security eye:** re-run a read-only security-review subagent after structural
  changes; keep `docs/reference/security.md` current. Enforced gates:
  no binding off `127.0.0.1` without auth+CSRF; never launch MCP subprocesses from
  UI-supplied `command`/`args` (server-side allowlist only).
- Secrets only in `.env`; never write them to `config.json`, logs, or API responses.

## Environment facts

- Device: ESPHome name `respeaker-xvf3800-assistant`, mDNS
  `respeaker-xvf3800-assistant.local`, native API port `6053`, no API encryption.
- Home Assistant: `http://192.168.18.31:8123`. Reachable via the `home-assistant`
  MCP server (`uvx ha-mcp@latest`) — the first MCP tool source for the agent.
- LLM/STT: Mistral OpenAI-compatible `https://api.mistral.ai/v1`. Chat
  `mistral-medium-latest` (small hallucinates tool calls — verify post-conditions).
  STT `voxtral-mini-latest`. TTS: Piper (`nl_NL-ronnie-medium`).
- Phase-2 caveat: only one client can be the device's voice handler — HA currently
  owns it; stop HA's pipeline for this device before the agent takes over voice.

## Design decisions (don't re-litigate)

- **Python** (aioesphomeapi has the native voice flow; Pillow for e-paper).
- **OpenAI-compatible `/chat/completions` + `tools`**, not Mistral's Agents API
  (provider-agnostic). Force tool use with `tool_choice:"any"` on must-act turns.
- **Verify the tool actually ran** — don't trust the model's "done".
