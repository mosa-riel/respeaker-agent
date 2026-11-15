# Reference — Security review

Findings from read-only security reviews (first one 2026-06-07, then after each structural
change), with their status. **Re-run the review after structural changes.**

## Threat model

A Python service that connects to an ESPHome device, serves a FastAPI web UI for status and
configuration, connects to MCP servers and calls LLM/STT/TTS endpoints. Attack surface: the
HTTP API (config writes, paid LLM calls), config and secret files, MCP server configuration,
host commands (Bluetooth, audio), and untrusted data from the device, the LLM, web search and
other tool results.

## Findings and status

| Sev | Finding | Status |
|---|---|---|
| CRITICAL | `PUT /api/config` has no auth/CSRF/CORS lockdown. | **Mitigated** by the `127.0.0.1` bind (ingress in the add-on) and by removing dangerous fields. **Gate:** auth + same-origin/CSRF before binding elsewhere. |
| CRITICAL | `McpServer(**s)` from UI JSON → MCP `command`/`args` = arbitrary code execution. | **Fixed**: `mcp_servers` is not writable via the config API; `POST /api/mcp` adds URL servers only. |
| HIGH | `web_host`/`web_port` writable via PUT → attacker sets `0.0.0.0`. | **Fixed**: removed from the writable allowlist. |
| HIGH | StaticFiles served without auth. | **Mitigated** by the localhost bind / ingress; same auth gate. |
| MEDIUM | No type/range validation on ports. | **Fixed**: `device_port` 1–65535 → 422; `Settings.load` coerces ints. |
| MEDIUM | Unknown JSON keys crash startup. | **Fixed**: filtered to known fields. |
| MEDIUM | CWD-relative `config.json`/`.env`. | **Backlog**: resolve to a fixed app dir. |
| MEDIUM | `last_error` reflected verbatim to the UI. | **Fixed**: `_safe_error()` trims. |
| MEDIUM | TTS/STT base URL and key are user-set; a hostile `tts_base_url` would receive the reply text and the Bearer key. | **Accepted** under the local-trust model, same as `llm_base_url`. Don't point endpoints at untrusted hosts. |
| LOW | `device_info` interpolated into HTML without escaping. | **Fixed**: `escapeHtml()`. |
| LOW | Google Fonts CDN dependency. | **Backlog**: self-host fonts. |
| INFO | Secrets env-only, frozen, never serialized or logged. | **Confirmed**. |
| INFO | A real API key was briefly written to the tracked `.env.example` during an edit. | **Fixed** before commit: moved to the gitignored `.env`, example restored to placeholders; never committed or pushed. |

## Reviews by change

### STT/TTS config (step 02)
- New writable fields (`stt_*`, `tts_*`) are URLs, model ids, a voice id, a format and
  rates: no exec, no network bind. `tts_provider` is validated to a fixed set; rates are
  range-checked. `mcp_servers` and `web_host`/`web_port` stay excluded.
- The Bearer key is sent only to the configured `tts_base_url`. Engine audio is decoded as
  float32/PCM with numpy `frombuffer`: no code path, no deserialization risk.

### Test chat `POST /api/run` (step 03)
- Unauthenticated like the rest of the API and spends paid LLM/TTS tokens per call.
  **Mitigated** by the localhost bind / ingress. **Gate:** same auth requirement before any
  other bind; consider a light rate limit. Tool dispatch only runs registered handlers.

### MCP layer (step 04)
- **Access control lives in the MCP server's credentials, not in the agent.** HA enforces
  what a token may do server-side, so a prompt-injected tool call is bounded by the token
  whatever the model attempts. The first design used a non-admin token (admin, config,
  add-on, restart and registry calls rejected by HA, but no area registry); step 06 moved
  to an admin token for the area registry and exposure list, and step 09 to the
  role-scoped Supervisor token. Tokens are never in `config.json`.
- **`enabled_tools` (per-tool curation in the UI) is not security**: it only hides tools
  from the model.
- **`POST /api/mcp` adds URL servers only** (403 on `command`/`args`). stdio servers would
  run a subprocess, so they may only come from the trusted `config.json`.
- `_result_to_json` flattens MCP output to text/structured fields: no eval, no
  deserialization into code.
- Dependencies are locked in `uv.lock`; SBOM in `sbom.json` ([open-source](open-source.md)).

### Home Assistant add-ons, MCP over HTTP (step 09)
- **The "bind off `127.0.0.1`" gate is satisfied by ingress, not by auth code.** The UI is
  served through HA ingress, authenticated by the HA frontend. The agent runs bridged, so
  `:8099` is not on the LAN; a guard (`RESPEAKER_INGRESS=1`) additionally rejects every
  client except the ingress proxy `172.30.32.2` (403). Outside an add-on the app still binds
  `127.0.0.1` by default.
- **Secrets** stay env-only: `addon-run.py` reads the `api_key` / `llm_api_key` options
  (HA `password` type) and exports them as `LLM/STT/TTS_API_KEY`; they are never written to
  `/data/config.json`.
- **MCP DNS-rebinding protection is disabled, bounded.** The SDK guard is localhost-only with
  no glob and returns 421 otherwise. It protects browsers; these are server-to-server
  endpoints. funbox, websearch and ha-mcp have no LAN port. `mcp-screen` publishes only its
  PNG host (`:8799`): a static image, no secrets, no MCP endpoint.
- **HA credential via `SUPERVISOR_TOKEN`** (default): `homeassistant_api: true` injects a
  token scoped by `hassio_role`, reachable only through the core proxy. A pasted long-lived
  token remains an explicit fallback.
- **`ha_call_service` is unrestricted** (backlog): an admin-capable token can call any
  service (e.g. `homeassistant.restart`), a real risk with web-search prompt injection.
  Planned: a service-domain allowlist in `mcp-homeassistant` (allow light, cover, climate,
  switch, fan, scene, script, media_player, lock; block `homeassistant.*`, `recorder.*`,
  `hassio.*`). Supervisor role scoping narrows but does not replace it.

### Host Bluetooth control and local audio sink (steps 10–11)
- **`bluetoothctl` is fenced** (`bluetooth.py`): opt-in behind `bluetooth_control` (tools not
  registered otherwise); fixed binary via `create_subprocess_exec` (argv list, **no shell**);
  sub-commands allowlisted in code (scan/devices/pair/trust/connect/disconnect/info); the
  only free argument is a MAC validated by the `_MAC` regex and re-emitted canonical, so a
  non-MAC (`"x; rm -rf /"`) is rejected before any spawn. Every call has a timeout and kill.
  This satisfies the "server-side allowlist, never UI command/args" gate.
- **`paplay`** (`local_play.py`): fixed binary, argv list, no shell; the sink name is a
  separate argv element (`--device <sink>`). Stdin is numpy-decoded PCM.
- **`audio_sink`** is a writable string; worst case audio plays to a wrong or missing sink.
- **`/api/bluetooth*`** call the same fenced `bt_*` coroutines and re-check
  `bluetooth_control` (403 when off; GET returns `{enabled:false}`). No surface beyond the
  ingress-gated API.
- **Residual:** the LLM tool loop can reach the Bluetooth tools, so prompt injection could
  trigger a scan or connect. Bounded: Bluetooth pairing only, MAC-gated, off by default.

### Turn log (0.4.37)
- `/data/turns.jsonl` + `GET /api/turns` keep what was said (transcripts, spoken answers,
  shortened tool arguments and results) across restarts, for evaluation. Same content and
  ingress-only gate as `/api/trace`; read-only, fixed file, `limit` capped; not written to
  logs. Secrets never enter the trace, so not the turn log either. It is part of the
  add-on's backups; ~2000 turns kept.

## Hardening gates

- **Bind off `127.0.0.1`:** satisfied in the add-on via ingress + the `172.30.32.2`-only
  guard. Any non-ingress off-localhost bind still requires auth + CSRF + CORS.
- **MCP subprocesses:** never take `command`/`args` from the UI; server-side allowlist of
  approved servers; consider sandboxing. Deployed MCPs are separate add-ons reached by URL,
  so the agent launches no subprocess for them.
- **HA `call_service`:** add a service-domain allowlist in `mcp-homeassistant`.
- **Production:** SBOM diffing in CI; fixed config dir; self-hosted fonts.
