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
