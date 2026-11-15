"""Turn log — one compact JSON record per voice turn, kept on disk to evaluate interactions.

The trace bus is in memory and cleared on restart; this keeps what matters for judging a
turn afterwards: what was heard, every tool call with its (shortened) arguments and result,
everything spoken and when, the model's final text (also when it was not spoken), errors,
and the outcome. Built from the trace events of the turn, so the pipeline needs no extra
bookkeeping.

File: `<config-dir>/turns.jsonl` (override with `RESPEAKER_TURNS`), trimmed to the last
`_MAX_LINES` turns. Served by `GET /api/turns`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .trace import TraceBus

_MAX_LINES = 2000
_SHORT = 400


def _path() -> Path:
    override = os.getenv("RESPEAKER_TURNS")
    if override:
        return Path(override)
    return Path(os.getenv("RESPEAKER_CONFIG", "config.json")).resolve().parent / "turns.jsonl"


def _short(x: Any, n: int = _SHORT) -> str:
    if isinstance(x, dict) and set(x) == {"structured"}:  # MCP result wrapper
        x = x["structured"]
    if isinstance(x, dict) and set(x) == {"result"}:
        x = x["result"]
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def summarize(events: list[dict[str, Any]], started: float, outcome: str, version: str,
              ended: float | None = None) -> dict[str, Any]:
    """Trace events of one turn (any order) → the turn record. Times are seconds since wake;
    `ended` = when the turn finished (default: its last event)."""
    rec: dict[str, Any] = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)), "version": version, "outcome": outcome,
        "wake": None, "heard": None, "rounds": 0, "tools": [], "spoken": [], "model_text": [], "errors": [],
        "notes": [],
    }
    open_calls: dict[str, list[dict[str, Any]]] = {}
    last = started
    for e in sorted(events, key=lambda e: e["t"]):
        at = round(e["t"] - started, 1)
        last = max(last, e["t"])
        stage, text, data = e["stage"], e.get("text") or "", e.get("data") or {}
        if stage == "wake":
            if rec["wake"] is not None:  # the next turn (a follow-up, or a wake word over this one)
                break
            rec["wake"] = text
        elif stage == "stt" and e.get("direction") == "in":
            rec["heard"], rec["heard_at"] = text, at
        elif stage == "llm-req":
            rec["rounds"] += 1
        elif stage == "llm-rsp" and text and text != "(tool call)":
            rec["model_text"].append({"at": at, "text": text})
        elif stage == "tool" and e.get("direction") == "out":
            call = {"at": at, "name": data.get("name", text), "args": _short(data.get("args", {}), 600)}
            rec["tools"].append(call)
            open_calls.setdefault(call["name"], []).append(call)
        elif stage == "tool" and e.get("direction") == "in":
            name = data.get("name", "")
            if open_calls.get(name):
                call = open_calls[name].pop(0)
                call["result"] = _short(data.get("result"))
                call["took"] = round(at - call["at"], 1)
        elif stage == "tts":
            rec["spoken"].append({"at": at, "text": text})
        elif stage == "error" or e.get("level") in ("warn", "error"):
            rec["errors"].append({"at": at, "text": text[:300]})
        elif stage == "info" and text.startswith("voice:") and "receiving audio" not in text:
            rec["notes"].append({"at": at, "text": text[:200]})
    rec["first_speech"] = rec["spoken"][0]["at"] if rec["spoken"] else None
    rec["duration"] = round((ended or last) - started, 1)
    return rec


class TurnLog:
    def __init__(self, trace: TraceBus, version: str) -> None:
        self._trace = trace
        self._version = version

    def record(self, started: float, outcome: str) -> None:
        """Write the turn that began at `started` (wall clock, the wake) with its outcome."""
        ended = time.time()
        events = [e for e in self._trace.recent(500) if started - 0.5 <= e["t"] <= ended]  # the wake is logged just before
        if not events:
            return
        rec = summarize(events, started, outcome, self._version, ended)
        p = _path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if p.stat().st_size > 4_000_000:  # rarely: keep the newest turns
                lines = p.read_text(encoding="utf-8").splitlines()[-_MAX_LINES:]
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as err:
            self._trace.emit("info", f"turn log not written: {err}", level="warn")


def latest(limit: int = 50, since: float = 0.0) -> list[dict[str, Any]]:
    """Newest last. `since` = only turns that began at or after this wall-clock time."""
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, _MAX_LINES)):]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if since and time.mktime(time.strptime(rec["t"], "%Y-%m-%dT%H:%M:%S")) < since:
            continue
        out.append(rec)
    return out
