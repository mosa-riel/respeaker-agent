"""Home Assistant add-on entrypoint for the reSpeaker agent.

Bridges HA add-on conventions to the app:
  - maps the `api_key` option → the LLM/STT/TTS secret env vars the app reads;
  - applies network-critical options (device_host, tts_audio_host) every boot;
  - seeds /data/config.json on first boot, enforces the ingress bind, then launches.

MCP servers are added by URL in the agent UI (http://<add-on hostname>:<port>/mcp) — the
agent runs bridged, so it resolves the add-on hostnames.

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


def main() -> None:
    opts = _load(OPTIONS)

    # API key → secret env (Secrets.from_env reads these; STT/TTS fall back to LLM key).
    # Provider-agnostic: any OpenAI-compatible endpoint's key (configured in config.json).
    key = opts.get("api_key") or opts.get("mistral_api_key") or ""
    if key:
        os.environ["LLM_API_KEY"] = key
        os.environ.setdefault("STT_API_KEY", key)
        os.environ.setdefault("TTS_API_KEY", key)
        print(f"[addon-run] api_key applied (len={len(key)}, …{key[-4:]})")
    else:
        print("[addon-run] WARNING: no api_key option set — LLM calls will 401")

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
    # Audio-out routing, enforced every boot from the add-on options page: which sink TTS
    # plays to, and whether the host bluetoothctl tools are exposed.
    cfg["audio_sink"] = opts.get("audio_sink", "") or ""
    cfg["bluetooth_control"] = bool(opts.get("bluetooth_control", False))
    print(
        f"[addon-run] applied from add-on options: device_host={cfg.get('device_host')!r} "
        f"tts_audio_host={cfg.get('tts_audio_host')!r} audio_sink={cfg.get('audio_sink')!r} "
        f"bluetooth_control={cfg.get('bluetooth_control')}"
    )
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
