"""Config = non-secret settings (config.json, editable from the UI) + secrets (.env).

Secrets (API keys, device PSK) are NEVER written to config.json or exposed by the
settings API — they live only in environment / .env.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = Path(os.getenv("RESPEAKER_CONFIG", "config.json"))


@dataclass
class McpServer:
    name: str
    # stdio: command + args; or url for http/sse servers
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool = True
    # Non-secret env for a stdio server. Secrets (tokens) stay in the agent's own
    # .env and are inherited by the subprocess via os.environ — never put them here
    # (config.json is not a secret store).
    env: dict[str, str] = field(default_factory=dict)
    # Per-tool enable list, managed from the UI (checked = enabled). EMPTY = expose
    # everything the server offers. Non-empty = expose ONLY these (the others are
    # hidden). This is tool CURATION — fewer tools = much lower latency and better
    # tool-selection on small models — NOT a security boundary. Real access control
    # belongs to the MCP server's own credentials (e.g. a non-admin Home Assistant
    # token). Names are the server's own (unqualified) tool names.
    enabled_tools: list[str] = field(default_factory=list)
    # Force specific tool arguments regardless of what the model passes
    # ({tool_name: {arg: value}}). E.g. {"ha_search_entities": {"include_hidden": false}}
    # so hidden HA entities never surface — enforced server-side, not via the prompt.
    tool_arg_overrides: dict = field(default_factory=dict)

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"


@dataclass
class Settings:
    """Editable, non-secret. Persisted to config.json."""

    device_host: str = "respeaker-xvf3800-assistant.local"
    device_port: int = 6053
    llm_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "mistral-medium-latest"
    # Reusable, generic guidance. The home's areas/devices and the actual tool
    # NAMES are NOT hardcoded here — they come from the home-assistant MCP server
    # and get injected per-conversation as a context block (phase 4). Keep this
    # prompt tool-name-agnostic.
    system_prompt: str = (
        "Je bent de spraakassistent van dit huis. Antwoord altijd in het Nederlands, "
        "in spreektaal en kort — je antwoord wordt hardop voorgelezen, dus geen "
        "opsommingen of opmaak.\n"
        "GROND ALLES IN ECHTE DATA — verzin NOOIT iets. Harde regels:\n"
        "- Noem alleen apparaten die in de lijst hieronder of in een toolresultaat "
        "voorkomen. Verzin nooit een apparaat, naam, aantal of ruimte.\n"
        "- Voor de actuele staat (aan/uit, temperatuur, stand, slot) roep je ALTIJD "
        "eerst een tool aan; gok nooit de staat.\n"
        "- Bestaat iets niet in de lijst of toolresultaten? Zeg dan dat je het niet "
        "kent — verzin geen aannemelijk antwoord.\n"
        "- Tel of groepeer alleen op basis van de lijst/toolresultaten, niet uit je "
        "hoofd. Twijfel je? Gebruik een tool of zeg dat je het niet zeker weet.\n"
        "- Vraag om een ruimte als een opdracht meerdere apparaten kan betreffen, "
        "tenzij er maar één van dat type is.\n"
        "Algemene kennisvragen (niet over dit huis) mag je gewoon eerlijk beantwoorden."
    )
    max_tool_rounds: int = 5
    # Low temperature → far less fabrication (sticks to the grounded list / tool
    # results). Voice control wants determinism, not creativity.
    llm_temperature: float = 0.1
    # Seconds between home-context (entity list) refreshes. Also refreshable on demand.
    home_context_refresh_sec: int = 900
    # STT — OpenAI-compatible transcription. Swap base_url to a localhost server later.
    stt_base_url: str = "https://api.mistral.ai/v1"
    stt_model: str = "voxtral-mini-latest"
    stt_language: str = "nl"  # force transcription language (no auto-detect drift)
    # Extra transcription params merged into the request (e.g. {"context_bias":
    # "Keukenkopjes,Eettafel"} to bias vocab/spelling/language). Lists/dicts are
    # JSON-encoded for the multipart form. Overrides the defaults above on conflict.
    stt_extra: dict = field(default_factory=dict)
    # TTS — pluggable endpoint. `tts_provider` selects the adapter; `tts_base_url`
    # lets you flip hosted (Voxtral) ↔ local (localhost) without code changes.
    #   provider "voxtral": Mistral speech API (base64 JSON), voice = your voice_id.
    #   provider "openai":  OpenAI-compatible /audio/speech (binary stream).
    tts_provider: str = "voxtral"
    tts_base_url: str = "https://api.mistral.ai/v1"
    tts_model: str = "voxtral-mini-tts-2603"
    tts_voice_id: str = ""  # your Studio-recorded voice; set after recording
    tts_format: str = "pcm"  # speed-first: pcm float32 streams (~0.8s TTFB vs ~3s wav)
    tts_pcm_rate: int = 24000  # Voxtral pcm output rate (float32 LE @ 24 kHz)
    tts_out_rate: int = 16000  # PCM rate pushed to the device speaker path
    web_host: str = "127.0.0.1"
    web_port: int = 8730
    # Take over the device's voice handler (wake→STT→agent→TTS). OFF by default:
    # only one client may own voice, and Home Assistant owns it while the device is
    # adopted there. Stop HA's pipeline for this device before enabling.
    voice_enabled: bool = False
    # Follow-up conversation: after the reply, re-open the mic without a new wake word
    # (announce API: plays the reply, awaits real playback end, then start_conversation
    # re-listens). Verified on the reSpeaker. The session ends when you stay silent
    # (~vad_prespeech_ms) — an end chime then signals it's closed.
    voice_followup: bool = True
    voice_end_chime: bool = True  # play a short chime when a follow-up session ends
    device_volume: float = 1.0  # set on the reSpeaker media player at connect (0–1)
    # Server-side end-of-speech detection (this firmware streams continuously and
    # expects the server to decide when the user stopped). Energy-based VAD on the
    # 16-bit mic PCM: speech when RMS > vad_threshold; finalize after vad_silence_ms
    # of trailing silence; hard cap at vad_max_ms; give up if no speech within
    # vad_prespeech_ms.
    vad_threshold: int = 500
    vad_silence_ms: int = 800
    vad_max_ms: int = 12000
    vad_prespeech_ms: int = 7000
    # TTS playback: this firmware fetches a URL and decodes by type — its pipeline is
    # FLAC (it rejects WAV: "Could not determine audio file type"). We ask the engine
    # for this format and serve it on the LAN audio server (random tokens).
    tts_audio_port: int = 8731
    tts_voice_format: str = "flac"  # device-playback format (flac|mp3|wav|opus)
    # Host the device should fetch TTS audio from. Empty = auto-detect the LAN IP on the
    # interface to the device (works when the agent is on the host network). When the
    # agent runs bridged (e.g. a HA add-on), its own IP isn't LAN-reachable — set this to
    # the host's LAN IP and publish tts_audio_port so the device can reach it.
    tts_audio_host: str = ""
    mcp_servers: list[McpServer] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            return cls()
        raw = json.loads(CONFIG_PATH.read_text())
        # Ignore unknown keys so a hand-edited / stale config.json can't crash startup.
        known = {f.name for f in fields(cls)} - {"mcp_servers"}
        server_defs = raw.get("mcp_servers", [])
        server_fields = {f.name for f in fields(McpServer)}
        servers = [
            McpServer(**{k: v for k, v in s.items() if k in server_fields})
            for s in server_defs
            if isinstance(s, dict)
        ]
        clean = {k: v for k, v in raw.items() if k in known}
        # Defensive coercion so a hand-edited/corrupt config.json can't crash boot.
        for int_key in ("device_port", "web_port"):
            if int_key in clean:
                try:
                    clean[int_key] = int(clean[int_key])
                except (TypeError, ValueError):
                    clean.pop(int_key)
        return cls(**clean, mcp_servers=servers)

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))


@dataclass(frozen=True)
class Secrets:
    """Secret, env-only. Never serialized to disk by this app or shown in the UI."""

    device_noise_psk: str | None
    device_password: str
    llm_api_key: str
    stt_api_key: str
    tts_api_key: str

    @classmethod
    def from_env(cls) -> "Secrets":
        llm_key = os.getenv("LLM_API_KEY", "")
        # STT/TTS default to the LLM key (same Mistral account); override per-endpoint
        # when pointing at a separate/local server.
        return cls(
            device_noise_psk=(os.getenv("RESPEAKER_NOISE_PSK") or None),
            device_password=os.getenv("RESPEAKER_PASSWORD", ""),
            llm_api_key=llm_key,
            stt_api_key=(os.getenv("STT_API_KEY") or llm_key),
            tts_api_key=(os.getenv("TTS_API_KEY") or llm_key),
        )
