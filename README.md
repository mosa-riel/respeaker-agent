# reSpeaker Agent

Custom voice agent for the reSpeaker XVF3800 — the path to owning the whole stack:
talk to the device directly, fan tools in from multiple MCP servers, and (later)
render custom screens to the reTerminal e-paper. Replaces the fragile nested HA
integration chain.

**Status:** Phase 1 — connect to the device + a local web UI for status/config.

## Stack
- Python ≥3.11, `aioesphomeapi` (ESPHome native API), FastAPI + a no-build static UI.
- Styling lifted from `mosa.cloud.sources/commander`.

For a full orientation (architecture, phases, conventions) read **`CLAUDE.md`** and
**`docs/PLAN.md`**.

## Run (laptop first)
```bash
cp .env.example .env            # device PSK (if any) + LLM_API_KEY
cp config.example.json config.json
make run                        # http://127.0.0.1:8730  (or: uv run respeaker-agent)
make sbom                       # regenerate sbom.json (also served at GET /api/sbom)
```

## Config & secrets
- **`config.json`** — non-secret settings, editable from the UI (device host/port,
  LLM base URL/model, web bind, MCP servers). Gitignored.
- **`.env`** — secrets only (`RESPEAKER_NOISE_PSK`, `LLM_API_KEY`). Never written to
  disk by the app, never returned by the API/UI. Gitignored.

## Roadmap
1. **Phase 1 (now):** device link + status/config UI.
2. **Phase 2:** voice flow — wake event → mic audio → STT → LLM(MCP tools) → TTS.
3. **Phase 3:** `show_screen` tool → render + push to reTerminal e-paper.
4. **Phase 4:** multi-MCP config UI; package as HA add-on.

## Security
The web UI edits config and binds a port; MCP servers execute tools. Keep
`web_host=127.0.0.1` unless you add auth. See `docs/` for the security review.
