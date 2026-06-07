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

Dependencies are now pinned via **`uv.lock`** (was a backlog item); SBOM at
`sbom.json`. See [open-source.md](open-source.md).

## Hardening gates

- **Bind off `127.0.0.1`:** auth + CSRF + same-origin + CORS lockdown on `/api/*` writes.
- **Launch MCP subprocesses:** never take `command`/`args` from UI; server-side
  allowlist of approved servers; consider sandboxing.
- **Prod:** SBOM diffing in CI; fixed config dir; self-hosted fonts.
