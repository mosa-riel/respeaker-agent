"""Captured-utterance store — save the exact mic PCM we send to STT as a WAV, so the
quality can be heard back in the UI (diagnosing bad transcriptions).

Files live next to config.json (`<config-dir>/recordings/`, override with
`RESPEAKER_RECORDINGS`), capped to the last `_MAX`. Each utterance is `<ts>.wav` with a
`<ts>.txt` sidecar holding the transcript. Names are timestamps; the API validates a
strict pattern before serving so a name can't escape the dir.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import numpy as np

from . import audio

_MAX = 20
_NAME = re.compile(r"^\d{8}-\d{6}-\d{3}$")  # YYYYmmdd-HHMMSS-mmm


def _dir() -> Path:
    override = os.getenv("RESPEAKER_RECORDINGS")
    if override:
        d = Path(override)
    else:
        cfg = os.getenv("RESPEAKER_CONFIG", "config.json")
        d = Path(cfg).resolve().parent / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    t = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{int(t * 1000) % 1000:03d}"


def save(pcm16: bytes, rate: int, text: str) -> str | None:
    """Write the utterance + transcript sidecar, evict old. Returns the base name."""
    try:
        d = _dir()
        name = _stamp()
        samples = np.frombuffer(pcm16, dtype="<i2")
        (d / f"{name}.wav").write_bytes(audio.int16_to_wav_bytes(samples, rate))
        (d / f"{name}.txt").write_text(text or "", encoding="utf-8")
        _evict(d)
        return name
    except Exception:  # noqa: BLE001 - recording is best-effort, never break a turn
        return None


def _evict(d: Path) -> None:
    wavs = sorted(d.glob("*.wav"))
    for old in wavs[:-_MAX]:
        old.unlink(missing_ok=True)
        old.with_suffix(".txt").unlink(missing_ok=True)


def latest(n: int = 20) -> list[dict]:
    """Newest-first list of {name, text, seconds, bytes}."""
    d = _dir()
    out = []
    for wav in sorted(d.glob("*.wav"), reverse=True)[:n]:
        size = wav.stat().st_size
        txt = wav.with_suffix(".txt")
        out.append({
            "name": wav.stem,
            "text": txt.read_text(encoding="utf-8") if txt.exists() else "",
            "bytes": size,
            "seconds": round(max(0, size - 44) / 2 / 16000, 1),  # 16k/16-bit mono est.
        })
    return out


def path_for(name: str) -> Path | None:
    """Validated WAV path for `name`, or None if the name is malformed/missing."""
    if not _NAME.match(name or ""):
        return None
    p = _dir() / f"{name}.wav"
    return p if p.exists() else None
