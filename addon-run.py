"""Home Assistant add-on entrypoint for the reSpeaker agent.

Bridges HA add-on conventions to the app:
  - maps the `api_key` option → the LLM/STT/TTS secret env vars the app reads;
  - applies network-critical options (device_host, tts_audio_host) every boot;
  - discovers each MCP add-on's real internal hostname via the Supervisor API and
    rewrites the mcp_servers urls (the agent runs bridged, so add-on DNS resolves);
  - seeds /data/config.json on first boot, enforces the ingress bind, then launches.

Secrets live only in env (never written to config.json), same as the .env flow.
"""

from __future__ import annotations

import json
import os
import shutil

OPTIONS = "/data/options.json"      # HA-managed add-on options
CONFIG = "/data/config.json"        # persistent app config (survives updates)
# Shipped deployment config. config.json is gitignored, so a clean build context may only
# have the example — fall back to it.
SEEDS = ("/app/config.json", "/app/config.example.json")


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Our MCP add-ons: config-server-name → (slug suffix, MCP port). Add-on install gives a
# hashed hostname prefix per repo, so we can't hardcode the url — discover it. The agent
# runs BRIDGED, so it can both query the Supervisor and resolve add-on hostnames.
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
    except Exception as err:  # noqa: BLE001 - discovery is optional
        print(f"[addon-run] MCP discovery skipped: {err}")
        return

    host_by_suffix: dict[str, str] = {}
    for a in addons:
        slug, host = a.get("slug", ""), a.get("hostname")
        if not host:
            continue
        for suffix, _port in _MCP_ADDONS.values():
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
            print(f"[addon-run] MCP '{srv['name']}' → {srv['url']}")


def main() -> None:
    opts = _load(OPTIONS)

    # API key → secret env (Secrets.from_env reads these; STT/TTS fall back to LLM key).
    # Provider-agnostic: any OpenAI-compatible endpoint's key (configured in config.json).
    key = opts.get("api_key") or opts.get("mistral_api_key") or ""
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
    # Network-critical options, enforced every boot: bridged means the device must be
    # reached by IP (no mDNS) and the device must fetch TTS audio from the host's LAN IP.
    for k in ("device_host", "tts_audio_host"):
        if opts.get(k):
            cfg[k] = opts[k]
    # Rewrite MCP urls to the real (discovered) add-on hostnames.
    _discover_mcp_urls(cfg)
    if first_boot:
        # Seed a few user-facing fields from options once; afterwards the UI owns them.
        for k in ("tts_voice_id", "llm_model"):
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
