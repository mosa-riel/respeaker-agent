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


@dataclass
class Settings:
    """Editable, non-secret. Persisted to config.json."""

    device_host: str = "respeaker-xvf3800-assistant.local"
    device_port: int = 6053
    llm_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "mistral-medium-latest"
    web_host: str = "127.0.0.1"
    web_port: int = 8730
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

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            device_noise_psk=(os.getenv("RESPEAKER_NOISE_PSK") or None),
            device_password=os.getenv("RESPEAKER_PASSWORD", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
        )
