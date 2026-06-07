"""Trace bus — the single stream of everything moving through the pipeline.

Every stage (device, wake word, STT, LLM request/response, tool call/result, TTS,
errors) emits a structured event here. The web layer exposes a snapshot
(`/api/trace`) and a live SSE stream (`/api/trace/stream`) so the UI can watch it
in real time. Phase-2 pipeline code just calls `bus.emit(...)`.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any, AsyncIterator

# Canonical stages, in pipeline order. The UI colours by these.
STAGES = ("device", "wake", "stt", "llm-req", "llm-rsp", "tool", "tts", "info", "error")


class TraceBus:
    def __init__(self, maxlen: int = 500) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._seq = itertools.count(1)

    def emit(
        self,
        stage: str,
        text: str,
        *,
        direction: str = "",
        data: Any | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        """Record one pipeline event. `data` is any JSON-serialisable detail
        (full prompt, raw response, tool args, error payload)."""
        evt = {
            "id": next(self._seq),
            "t": time.time(),
            "stage": stage,
            "direction": direction,  # "in" | "out" | ""
            "level": level,          # "info" | "warn" | "error"
            "text": text,
            "data": data,
        }
        self._buf.appendleft(evt)
        for q in list(self._subs):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # slow consumer; it can re-sync via /api/trace
        return evt

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(itertools.islice(self._buf, limit))

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.discard(q)
