"""STT — OpenAI-compatible speech-to-text (Voxtral now).

Takes the device's buffered mic PCM (16-bit mono @ in_rate), wraps it as WAV, and
POSTs it to `{stt_base_url}/audio/transcriptions`. Swap `stt_base_url` to a local
server later; same shape.
"""

from __future__ import annotations

import json

import httpx
import numpy as np

from . import audio
from .config import Secrets, Settings
from .trace import TraceBus

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class STTClient:
    def __init__(self, settings: Settings, secrets: Secrets, trace: TraceBus) -> None:
        self._s = settings
        self._key = secrets.stt_api_key
        self._trace = trace

    async def transcribe(self, pcm16: bytes, *, in_rate: int = 16000, language: str | None = None) -> str:
        """pcm16 = raw 16-bit signed mono PCM. Returns the transcript text."""
        language = language or self._s.stt_language
        samples = np.frombuffer(pcm16, dtype="<i2")
        if self._s.stt_normalize and samples.size:
            peak = int(np.max(np.abs(samples)))
            if peak > 0:
                gain = min(0.95 * 32767 / peak, max(1.0, self._s.stt_gain_max))
                if gain > 1.05:  # only boost meaningfully-quiet audio
                    samples = np.clip(samples.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
                    self._trace.emit("stt", f"genormaliseerd ×{gain:.1f}", direction="out",
                                     data={"peak_before": peak, "gain": round(gain, 2)})
        wav = audio.int16_to_wav_bytes(samples, in_rate)
        url = f"{self._s.stt_base_url.rstrip('/')}/audio/transcriptions"
        files = {"file": ("audio.wav", wav, "audio/wav")}
        data: dict = {"model": self._s.stt_model, "language": language}
        if self._s.stt_prompt:
            data["prompt"] = self._s.stt_prompt  # bias language/vocab on short clips
        # Merge user-supplied extra params (context_bias, etc.); JSON-encode complex
        # values for the multipart form. These override the defaults on conflict.
        for k, v in (self._s.stt_extra or {}).items():
            data[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
                resp = await cli.post(url, files=files, data=data, headers=headers)
                resp.raise_for_status()
                text = (resp.json().get("text") or "").strip()
        except httpx.HTTPError as err:
            self._trace.emit("error", f"STT request failed: {_safe(err)}", level="error")
            raise
        self._trace.emit("stt", text or "(empty)", direction="in", data={"model": self._s.stt_model, "bytes": len(pcm16)})
        return text


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
