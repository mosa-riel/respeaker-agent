"""Realtime turn detection — stream the mic to Mistral's realtime transcription while the
user talks, so the model decides when the turn ends (it waits through a thinking pause,
ends after a finished sentence) instead of a fixed silence window.

Hybrid by design: this stream only decides WHEN the turn ends; its own text is traced for
comparison. The final transcript still comes from the batch STT model on the buffered
audio, which was more accurate on this device's audio (docs/steps/13).

Protocol (`wss://<stt host>/v1/audio/transcriptions/realtime?model=…`, Bearer auth):
  client → session.update, input_audio.append {audio: base64 pcm_s16le}, input_audio.flush,
           input_audio.end
  server → session.created, transcription.text.delta, turn.start / turn.eager_end /
           turn.resume / turn.end, transcription.done, error
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Callable
from urllib.parse import urlencode

import websockets

from .config import Settings
from .trace import TraceBus

_CONNECT_TIMEOUT = 5.0


class RealtimeTurn:
    """One utterance's realtime stream. Feed it with `send()`; it calls `on_end(spoke)`
    once when the model reports the end of the turn. On any failure `failed` turns True
    and the caller falls back to its own silence VAD."""

    def __init__(self, settings: Settings, key: str, trace: TraceBus, on_end: Callable[[bool], None]) -> None:
        self._s = settings
        self._key = key
        self._trace = trace
        self._on_end = on_end
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.failed = False
        self.started = False  # the model heard speech (turn.start)
        self.text = ""
        self._ended = False
        self._task = asyncio.create_task(self._run())

    def send(self, pcm: bytes) -> None:
        if not self.failed and not self._ended:
            self._queue.put_nowait(pcm)

    def close(self) -> None:
        """Stop streaming (the turn was finalized elsewhere); never raises."""
        self._ended = True
        self._queue.put_nowait(None)
        loop = asyncio.get_running_loop()
        loop.call_later(3.0, self._task.cancel)

    def _url(self) -> str:
        base = self._s.stt_base_url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/audio/transcriptions/realtime?{urlencode({'model': self._s.stt_realtime_model})}"

    def _session(self) -> dict:
        session: dict = {
            "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
            "language": self._s.stt_language,
            "turn_detection": {"min_silence_ms": self._s.stt_realtime_min_silence_ms},
        }
        bias = (self._s.stt_extra or {}).get("context_bias")
        if isinstance(bias, list) and bias:
            session["context_bias"] = bias
        return session

    async def _run(self) -> None:
        sender: asyncio.Task | None = None
        try:
            async with websockets.connect(self._url(), additional_headers={"Authorization": f"Bearer {self._key}"},
                                          open_timeout=_CONNECT_TIMEOUT, max_size=2**20) as ws:
                while True:  # handshake: wait for session.created
                    msg = json.loads(await asyncio.wait_for(ws.recv(), _CONNECT_TIMEOUT))
                    if msg.get("type") == "error":
                        raise RuntimeError(_error_text(msg))
                    if msg.get("type") == "session.created":
                        break
                await ws.send(json.dumps({"type": "session.update", "session": self._session()}))
                sender = asyncio.create_task(self._send(ws))
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "transcription.text.delta":
                        self.text += msg.get("text", "")
                    elif kind == "turn.start":
                        self.started = True
                    elif kind == "turn.end":
                        self._finish()
                        return
                    elif kind == "error":
                        raise RuntimeError(_error_text(msg))
                    elif kind == "transcription.done":
                        return
        except asyncio.CancelledError:
            pass
        except Exception as err:  # noqa: BLE001 - realtime is an enhancement; fall back quietly
            if not self._ended:
                self.failed = True
                self._trace.emit("info", f"realtime STT unavailable, using silence VAD: {str(err)[:100]}")
        finally:
            if sender is not None:
                sender.cancel()

    def _finish(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._trace.emit("stt", f"realtime einde beurt: {self.text.strip() or '(geen tekst)'}", direction="in",
                         data={"model": self._s.stt_realtime_model, "text": self.text.strip()})
        self._on_end(self.started or bool(self.text.strip()))

    async def _send(self, ws) -> None:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                await ws.send(json.dumps({"type": "input_audio.flush"}))
                await ws.send(json.dumps({"type": "input_audio.end"}))
                return
            await ws.send(json.dumps({"type": "input_audio.append", "audio": base64.b64encode(chunk).decode("ascii")}))


def _error_text(msg: dict) -> str:
    err = msg.get("error")
    if isinstance(err, dict):
        inner = err.get("message")
        if isinstance(inner, dict):
            return str(inner.get("detail") or inner)
        return str(inner or err)
    return str(err or msg)[:200]
