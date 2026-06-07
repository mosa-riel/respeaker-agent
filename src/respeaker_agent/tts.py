"""TTS — pluggable text-to-speech behind one interface.

The engine flips hosted (Voxtral) ↔ local (a localhost server) by config alone:
change `tts_base_url` (+ `tts_provider`), nothing else. Adapters all expose the same
`synth_stream(text) -> AsyncIterator[bytes]`, yielding 16-bit mono PCM chunks at
`settings.tts_out_rate`, ready to hand straight to `send_voice_assistant_audio`.

Speed over quality: Voxtral is asked for `pcm` (float32 @ 24 kHz, ~0.8s
time-to-first-audio) with `stream:true`, decoded + resampled to the device rate
as chunks arrive — lowest perceived lag. A generic OpenAI-compatible `/audio/speech`
adapter is provided for the local-swap case.
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx

from . import audio
from .config import Secrets, Settings
from .trace import TraceBus

# Cap per-request text; long replies should be sentence-chunked upstream.
_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


class TTSClient(ABC):
    def __init__(self, settings: Settings, secrets: Secrets, trace: TraceBus) -> None:
        self._s = settings
        self._key = secrets.tts_api_key
        self._trace = trace

    @abstractmethod
    def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield 16-bit mono PCM chunks at settings.tts_out_rate."""
        ...

    async def synth(self, text: str) -> bytes:
        """Convenience: collect the whole stream into one PCM blob."""
        chunks = [c async for c in self.synth_stream(text)]
        return b"".join(chunks)


class VoxtralTTS(TTSClient):
    """Mistral Voxtral speech API. POST {base}/audio/speech.

    Streaming pcm: each SSE/line chunk carries base64 float32 LE @ tts_pcm_rate;
    non-streaming: a single JSON {"audio_data": <b64>}. Both decoded → int16 →
    resampled to the device rate.
    """

    async def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        url = f"{self._s.tts_base_url.rstrip('/')}/audio/speech"
        body = {
            "model": self._s.tts_model,
            "input": text,
            "voice_id": self._s.tts_voice_id or None,
            "response_format": self._s.tts_format,  # "pcm"
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        self._trace.emit("tts", text, direction="out", data={"engine": "voxtral", "model": self._s.tts_model})
        src, dst = self._s.tts_pcm_rate, self._s.tts_out_rate
        carry = b""  # leftover bytes that don't complete a float32 (4-byte) sample
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
                async with cli.stream("POST", url, json=body, headers=headers) as resp:
                    resp.raise_for_status()
                    async for raw in _iter_b64_pcm(resp):
                        carry += raw
                        usable = len(carry) - (len(carry) % 4)
                        if usable <= 0:
                            continue
                        block, carry = carry[:usable], carry[usable:]
                        out = audio.resample_int16(audio.float32le_to_int16(block), src, dst)
                        if out.size:
                            yield out.tobytes()
        except httpx.HTTPError as err:
            self._trace.emit("error", f"TTS request failed: {_safe(err)}", level="error")
            raise


class OpenAITTS(TTSClient):
    """Generic OpenAI-compatible /audio/speech. Returns a binary audio body
    (wav/pcm). Used when pointing at a local server. WAV is parsed for its rate;
    raw pcm is assumed float32 @ tts_pcm_rate."""

    async def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        url = f"{self._s.tts_base_url.rstrip('/')}/audio/speech"
        fmt = self._s.tts_format
        body = {
            "model": self._s.tts_model,
            "input": text,
            "voice": self._s.tts_voice_id or "alloy",
            "response_format": "wav" if fmt not in ("wav", "pcm") else fmt,
        }
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        self._trace.emit("tts", text, direction="out", data={"engine": "openai", "model": self._s.tts_model})
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
                resp = await cli.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.content
        except httpx.HTTPError as err:
            self._trace.emit("error", f"TTS request failed: {_safe(err)}", level="error")
            raise
        if body["response_format"] == "wav":
            samples, rate = audio.wav_to_int16_mono(data)
        else:
            samples, rate = audio.float32le_to_int16(data), self._s.tts_pcm_rate
        out = audio.resample_int16(samples, rate, self._s.tts_out_rate)
        if out.size:
            yield out.tobytes()


async def _iter_b64_pcm(resp: httpx.Response) -> AsyncIterator[bytes]:
    """Yield decoded audio bytes from a Voxtral speech response, streaming or not.

    Streaming responses are newline/SSE-delimited JSON objects each carrying a
    base64 `audio_data` (or OpenAI-style `{"data": "..."}`); a non-streaming
    response is a single JSON object. Lines without audio are skipped.
    """
    buf = ""
    got_any = False
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        b64 = _extract_audio(line)
        if b64:
            got_any = True
            yield base64.b64decode(b64)
        else:
            buf += line  # may be a single JSON spread across lines
    if not got_any and buf:
        b64 = _extract_audio(buf)
        if b64:
            yield base64.b64decode(b64)


def _extract_audio(s: str) -> str | None:
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        return obj.get("audio_data") or obj.get("data") or obj.get("audio")
    return None


def _safe(err: Exception) -> str:
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]


def make_tts(settings: Settings, secrets: Secrets, trace: TraceBus) -> TTSClient:
    provider = (settings.tts_provider or "voxtral").lower()
    if provider == "voxtral":
        return VoxtralTTS(settings, secrets, trace)
    if provider in ("openai", "openai-compatible"):
        return OpenAITTS(settings, secrets, trace)
    raise ValueError(f"unknown tts_provider: {settings.tts_provider!r} (want voxtral|openai)")
