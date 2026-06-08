"""Home Assistant add-on entrypoint for the reSpeaker agent.

Bridges HA add-on conventions to the app:
  - maps the `mistral_api_key` option → the LLM/STT/TTS secret env vars the app reads;
  - seeds a persistent /data/config.json from the shipped deployment config on first
    boot (user options applied once), then always enforces the ingress network bind;
  - turns on ingress mode, then launches the normal CLI entrypoint.

Secrets live only in env (never written to config.json), same as the .env flow.
"""

from __future__ import annotations

import json
import os
import shutil

OPTIONS = "/data/options.json"      # HA-managed add-on options
CONFIG = "/data/config.json"        # persistent app config (survives updates)
# Shipped deployment config (HTTP MCP urls). config.json is gitignored, so a clean build
# context may only have the example — fall back to it.
SEEDS = ("/app/config.json", "/app/config.example.json")


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Our MCP add-ons: config-server-name → (slug suffix, MCP port). Git-repo installs give
# add-ons a hashed hostname prefix, so we can't hardcode the url — discover it instead.
_MCP_ADDONS = {
    "home-assistant": ("mcp_homeassistant", 8086),
    "funbox": ("mcp_funbox", 8785),
    "websearch": ("mcp_websearch", 8786),
    "screen": ("mcp_screen", 8788),
}


def _discover_mcp_urls(cfg: dict) -> None:
    """Ask the Supervisor for each installed add-on's real hostname and rewrite the
    matching mcp_servers url. Best-effort: no-op without SUPERVISOR_TOKEN / off-HA."""
    import urllib.request

    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return
    try:
        req = urllib.request.Request(
            "http://supervisor/addons", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 - fixed supervisor host
            addons = json.load(r).get("data", {}).get("addons", [])
    except Exception:  # noqa: BLE001 - discovery is optional
        return

    # slug suffix -> real hostname (e.g. "mcp_funbox" -> "abc123-mcp-funbox")
    host_by_suffix: dict[str, str] = {}
    for a in addons:
        slug, host = a.get("slug", ""), a.get("hostname")
        if not host:
            continue
        for _name, (suffix, _port) in _MCP_ADDONS.items():
            if slug == suffix or slug.endswith(f"_{suffix}"):
                host_by_suffix[suffix] = host

    for srv in cfg.get("mcp_servers", []):
        entry = _MCP_ADDONS.get(srv.get("name"))
        if not entry:
            continue
        suffix, port = entry
        host = host_by_suffix.get(suffix)
        if host:
            srv["url"] = f"http://{host}:{port}/mcp"


def main() -> None:
    opts = _load(OPTIONS)

    # API key → secret env (Secrets.from_env reads these; STT/TTS fall back to LLM key).
    key = opts.get("mistral_api_key") or ""
    if key:
        os.environ["LLM_API_KEY"] = key
        os.environ.setdefault("STT_API_KEY", key)
        os.environ.setdefault("TTS_API_KEY", key)

    first_boot = not os.path.exists(CONFIG)
    if first_boot:
        seed = next((s for s in SEEDS if os.path.exists(s)), None)
        if seed:
            shutil.copy(seed, CONFIG)

    cfg = _load(CONFIG)
    # Always enforce the ingress bind — the agent must listen where HA's ingress proxy
    # reaches it, never on a guessable LAN port without the proxy guard.
    cfg["web_host"] = "0.0.0.0"
    cfg["web_port"] = 8099
    # Self-correct MCP urls from the actual installed add-on hostnames.
    _discover_mcp_urls(cfg)
    if first_boot:
        # Seed a few user-facing fields from options once; afterwards the UI owns them.
        for k in ("device_host", "tts_voice_id", "llm_model"):
            if opts.get(k):
                cfg[k] = opts[k]
        if "voice_enabled" in opts:
            cfg["voice_enabled"] = bool(opts["voice_enabled"])
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)

    os.environ["RESPEAKER_CONFIG"] = CONFIG
    os.environ["RESPEAKER_INGRESS"] = "1"

    from respeaker_agent.cli import main as run

    run()


if __name__ == "__main__":
    main()
