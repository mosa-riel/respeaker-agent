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
    # The add-on Configuration page is the SOURCE OF TRUTH for all scalar settings: every
    # one is enforced into config.json every boot. The web UI only owns what HA's static
    # form can't do well — tts_voice_id (live dropdown), system_prompt, stt_extra (JSON),
    # mcp_servers — so those are NOT listed here and survive across restarts.
    SCALAR_OPTS = (
        "device_host", "device_port", "llm_base_url", "llm_model", "llm_temperature",
        "max_tool_rounds", "home_context_refresh_sec", "stt_base_url", "stt_model",
        "stt_language", "stt_normalize", "stt_gain_max", "stt_prompt",
        "tts_provider", "tts_base_url", "tts_model", "tts_format",
        "tts_pcm_rate", "tts_out_rate", "tts_voice_format", "tts_audio_port",
        "tts_audio_host", "voice_enabled", "voice_followup", "voice_end_chime",
        "device_volume", "vad_threshold", "vad_silence_ms", "vad_max_ms",
        "vad_prespeech_ms", "audio_sink", "bt_lead_silence_ms", "bluetooth_control",
        "save_recordings",
    )
    for k in SCALAR_OPTS:
        if k in opts:
            cfg[k] = opts[k]
    print(
        f"[addon-run] applied {len(SCALAR_OPTS)} scalar options from add-on config: "
        f"device_host={cfg.get('device_host')!r} voice_enabled={cfg.get('voice_enabled')} "
        f"audio_sink={cfg.get('audio_sink')!r} bluetooth_control={cfg.get('bluetooth_control')}"
    )
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)

    os.environ["RESPEAKER_CONFIG"] = CONFIG
    os.environ["RESPEAKER_INGRESS"] = "1"

    from respeaker_agent.cli import main as run

    run()


if __name__ == "__main__":
    main()
