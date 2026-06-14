# Reference — Security review

> Living doc. Findings from a read-only security-review subagent (2026-06-07), with
> fixed / backlog status. **Re-run the review after structural changes** — the user
> wants a security eye kept on this project.

## Threat model

Local Python service: connects to an ESPHome device, serves a FastAPI web UI for
status/config, and will (phase 2+) launch MCP servers and call LLM endpoints.
Attack surface: the HTTP API (config writes), config/secret files, MCP subprocess
launching (future), and untrusted data from the device + LLM.

## Findings & status

| Sev | Finding | Status |
|---|---|---|
| 🔴 CRITICAL | `PUT /api/config` has no auth/CSRF/CORS lockdown. | **Mitigated** by `127.0.0.1` bind + removing dangerous fields. **Gate:** auth + same-origin/CSRF before binding elsewhere. |
| 🔴 CRITICAL | `McpServer(**s)` from UI JSON → MCP `command`/`args` = arbitrary code execution. | **Fixed** — `mcp_servers` not writable via API; edit `config.json` directly until a server-side allowlist exists. |
| 🟠 HIGH | `web_host`/`web_port` writable via PUT → attacker sets `0.0.0.0`. | **Fixed** — removed from writable allowlist (verified ignored). |
| 🟠 HIGH | StaticFiles + no auth. | **Mitigated** by localhost bind; same auth gate. |
| 🟡 MEDIUM | No type/range validation on ports. | **Fixed** — `device_port` 1–65535 → 422; `Settings.load` coerces ints. |
| 🟡 MEDIUM | Unknown JSON keys crash startup. | **Fixed** — filtered to known fields. |
| 🟡 MEDIUM | CWD-relative `config.json`/`.env`. | **Backlog** — resolve to a fixed app dir. |
| 🟡 MEDIUM | `last_error` reflected verbatim to UI. | **Fixed** — `_safe_error()` trims. |
| 🟢 LOW | `device_info` interpolated without escaping. | **Fixed** — `escapeHtml()`. |
| 🟢 LOW | Google Fonts CDN dependency. | **Backlog** — self-host fonts. |
| 🟢 INFO | Secrets env-only, frozen, never serialized/logged. | **Confirmed**. |
| 🟡 MEDIUM | TTS/STT base_url + key are user-set (UI/config). A hostile `tts_base_url` would receive the reply text + Bearer key. | **Accepted** — local-trust model; same as `llm_base_url`. Key falls back to `LLM_API_KEY`. Don't point at untrusted hosts. |
| 🟢 INFO | Real Mistral key briefly written to `.env.example` (tracked template) during an edit. | **Fixed** — moved to gitignored `.env`, example restored to placeholders; never committed/pushed. |

### Phase-2 additions reviewed (TTS/audio/config — step 02)

- New writable config fields (`stt_*`, `tts_*`) are URLs / model ids / a voice id /
  format / rates — no exec, no network bind. `tts_provider` validated to a fixed
  set; rate fields range-checked. `mcp_servers`, `web_host/web_port` still excluded.
- `tts.py` builds the request URL from the user-set `tts_base_url`; the Bearer key
  is sent only to that host — acceptable under the localhost-trust model (matches
  `llm_base_url`). Audio bytes from the engine are decoded as float32/PCM only
  (numpy `frombuffer`) — no code path, no deserialization risk.
### MCP layer (step 04)

- **Access control lives in the MCP server's credentials, not the agent.** For
  Home Assistant that means a **non-admin long-lived token** in `.env` — HA rejects
  admin/config/add-on/restart/registry calls server-side, so a prompt-injected
  admin tool call fails at HA regardless of what the model attempts. The token is
  never written to `config.json`; the stdio subprocess inherits it via `os.environ`.
  Trade-off accepted: a non-admin token also can't read the area/floor registry.
- **`disabled_tools` (UI toggle) is curation, NOT security** — it only hides tools
  from the model; a determined caller bypassing the agent is still bounded by the
  token. Documented as such in code + UI.
- **`POST /api/mcp` adds remote (url) servers only.** stdio servers run a
  subprocess, so their `command`/`args` must come from `config.json` (trusted
  file), never the unauthenticated API. Enforced (403 on command/args in payload).
- `_result_to_json` flattens MCP tool output to text/structured fields — no eval,
  no deserialization of server-controlled objects into code.

- `POST /api/run` (step 03) is unauthenticated like the rest of the API and calls
  the paid LLM/TTS per request. **Mitigated** by the `127.0.0.1` bind. **Gate:**
  same auth requirement as other writes before any non-localhost bind; consider a
  light rate-limit since each call spends LLM+TTS tokens. Tool dispatch only runs
  registered handlers (no UI-supplied callables); demo tools are pure/no-exec.

Dependencies are now pinned via **`uv.lock`** (was a backlog item); SBOM at
`sbom.json`. See [open-source.md](open-source.md).

### HA add-ons + MCP-over-HTTP split (step 09) — reviewed

See [deployment.md](deployment.md) for the architecture.

- **The "bind off `127.0.0.1`" gate is now satisfied — via ingress, not auth code.** The
  agent add-on serves its UI through **HA ingress** (`ingress: true`), which is
  authenticated by the HA frontend; no separate LAN port and no in-app auth needed.
  Because `host_network: true` also exposes `:8099` on the LAN, a guard middleware
  (`RESPEAKER_INGRESS=1`) **rejects every client except the ingress proxy `172.30.32.2`
  (403)**. Net: unauthenticated LAN access to `/api/*` is closed in the deployed add-on.
  *(Running outside an add-on still binds `127.0.0.1` by default — unchanged.)*
- **Secrets** still env-only: `addon-run.py` reads the `mistral_api_key` add-on option
  (HA stores it as a `password` type) and exports it as `LLM/STT/TTS_API_KEY`; it is
  **never** written to `config.json` (which lives in `/data`). Confirmed.
- **MCP transport security disabled — bounded.** Each MCP server sets
  `enable_dns_rebinding_protection=False` (the SDK guard is localhost-only with no glob
  and 421s otherwise). DNS-rebinding protection defends *browsers*; these are
  server↔server endpoints. funbox/websearch/ha-mcp are **bridged, internal-only** (no
  LAN port). `mcp-screen` publishes only its **PNG host** (`:8799`) to the LAN — a static
  image, no secrets, no MCP endpoint. The MCP endpoints themselves are never LAN-exposed.
- **HA-MCP credential via `SUPERVISOR_TOKEN`** (default) replaces the hand-made
  long-lived token: `homeassistant_api: true` injects a token the Supervisor scopes by
  `hassio_role`, reachable only through the core proxy. A pasted long-lived token remains
  an explicit fallback option. Either way the token is not in `config.json`.
- **`call_service` domain allowlist** (admin token can call any service — real risk with
  web-search prompt injection) is **still backlog**, now to be implemented inside
  `mcp-homeassistant`. The supervisor-token role-scoping narrows but does not replace it.
- MCP url servers added via `POST /api/mcp` remain url-only (no subprocess); the
  per-server connection now runs in its own task (no behavioural auth change).

### Host Bluetooth control + local audio sink (step 10)

New capability: the agent can play TTS to a host PulseAudio sink (`audio_sink`, e.g. a
paired BT speaker) and run `bluetoothctl` to scan/pair/connect/disconnect speakers.
Both touch the host — reviewed against the project's "never run UI-supplied commands"
gate:

- **`bluetoothctl` is fenced** (`bluetooth.py`): OPT-IN behind `bluetooth_control`
  (tools aren't registered otherwise); fixed binary spawned via `create_subprocess_exec`
  (argv list, **no shell**); the sub-command is allowlisted in code (scan/devices/pair/
  trust/connect/disconnect/info — callers can't choose); the only free argument is a MAC
  validated by `_MAC` regex and re-emitted canonical — a non-MAC (`"x; rm -rf /"`) returns
  an error before any spawn. Every call has a timeout + kill. This satisfies the
  "server-side allowlist, never UI command/args" gate (same shape as the MCP rule).
- **`paplay` local playback** (`local_play.py`): fixed binary, argv list, no shell. The
  only caller value is the sink name, passed as a distinct argv element (`--device <sink>`),
  never interpolated. PCM fed on stdin is numpy-decoded audio — no exec path.
- **`audio_sink` writable via config**: a string sink name; worst case is "audio plays to
  the wrong/nonexistent sink" (paplay errors, traced) — no exec, no bind. Acceptable.
- **`/api/bluetooth*` endpoints (step 11)** expose the same scan/connect/disconnect ops to
  the web UI. They call the identical fenced `bt_*` coroutines, and every handler re-checks
  `bluetooth_control` (403 when off; GET returns `{enabled:false}`). No new surface beyond
  the localhost/ingress-gated API already in the threat model — the MAC validation + no-shell
  fence is shared with the tools.
- **Residual:** `bluetooth_control` is reachable by the LLM tool loop, so web-search /
  HA prompt-injection could in theory trigger a scan/connect. Bounded — the actions are
  Bluetooth-pairing only (no data exfil, no code exec), MAC-gated, and off by default.
  Keep it off unless you want voice/UI speaker switching.

## Hardening gates

- **Bind off `127.0.0.1`:** ✅ satisfied in the add-on via **ingress + 172.30.32.2-only
  guard**. For any *non-ingress* off-localhost bind, still require auth + CSRF + CORS.
- **Launch MCP subprocesses:** never take `command`/`args` from UI; server-side
  allowlist of approved servers; consider sandboxing. *(Deployed MCPs are now separate
  add-ons reached by url — no subprocess launched by the agent at all.)*
- **HA `call_service`:** add a domain/service allowlist in `mcp-homeassistant`.
- **Prod:** SBOM diffing in CI; fixed config dir; self-hosted fonts.
