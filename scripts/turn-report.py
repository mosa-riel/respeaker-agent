#!/usr/bin/env python3
"""Evaluate voice turns from the turn log: one line per turn plus automatic flags.

Reads the JSON of `GET /api/turns` (or a turns.jsonl file) on stdin or from a path:

    curl -s <agent>/api/turns?limit=100 | scripts/turn-report.py
    scripts/turn-report.py /data/turns.jsonl

The flags point at turns worth a closer look; they are signals, not verdicts.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

SLOW_FIRST = 6.0  # s from transcript to the first spoken word
SLOW_TURN = 25.0  # s for the whole turn
FAILED = re.compile(r"niet gevonden|niet gelukt|geen plaats|fout|error|mislukt", re.I)
APPROX = re.compile(r"zelf is niet gevonden|gelijkende naam", re.I)
PROMISE = re.compile(r"op het scherm|laat .{0,40}zien|ik (zet|maak|teken)", re.I)
INFO = ("geo__", "staan__", "websearch__")
RENDER = ("show_card", "show_page", "show_dashboard", "show_html", "show_text", "show_image")


def _load(src: str) -> list[dict]:
    src = src.strip()
    if src.startswith("{") and '"turns"' in src[:20]:
        return json.loads(src)["turns"]
    if src.startswith("["):
        return json.loads(src)
    return [json.loads(line) for line in src.splitlines() if line.strip()]


def flags(t: dict) -> list[str]:
    out = []
    heard_at = t.get("heard_at") or 0
    if t.get("first_speech") is not None and t["first_speech"] - heard_at > SLOW_FIRST:
        out.append(f"eerste woord pas na {t['first_speech'] - heard_at:.0f} s")
    if (t.get("duration") or 0) > SLOW_TURN:
        out.append(f"beurt duurde {t['duration']:.0f} s")
    if t.get("heard") and not t.get("spoken") and t.get("outcome") != "none":
        out.append("niets uitgesproken")
    if t.get("outcome") == "cancelled":
        out.append("afgebroken")
    names = [c["name"] for c in t.get("tools", [])]
    for name, n in Counter(names).items():
        if n >= 3 and not name.endswith("ha_call_service"):
            out.append(f"{name.split('__')[-1]} {n}× aangeroepen")
    same = Counter((c["name"], c.get("args")) for c in t.get("tools", []))
    if any(n >= 2 for n in same.values()):
        out.append("zelfde call met zelfde argumenten herhaald")
    failed = [c for c in t.get("tools", []) if FAILED.search(c.get("result") or "")]
    if failed:
        out.append(f"{len(failed)} tool(s) zonder resultaat")
    if any(APPROX.search(c.get("result") or "") for c in t.get("tools", [])):
        out.append("alleen gelijkende naam gevonden: check of het antwoord dat zegt")
    rendered = [c for c in t.get("tools", []) if c["name"].split("__")[-1] in RENDER and not FAILED.search(c.get("result") or "")]
    spoken = " ".join(s["text"] for s in t.get("spoken", []))
    if PROMISE.search(spoken) and not rendered:
        out.append("belooft het scherm, maar er is niets getoond")
    if any(n.startswith(INFO) for n in names) and not rendered:
        out.append("informatievraag zonder kaart")
    said = {s["text"].strip() for s in t.get("spoken", [])}
    unspoken = [m["text"] for m in t.get("model_text", []) if m["text"].strip() not in said]
    if unspoken:
        out.append(f"niet uitgesproken modeltekst: {unspoken[-1][:60]!r}")
    if t.get("errors"):
        out.append(f"{len(t['errors'])} fout(en): {t['errors'][0]['text'][:60]!r}")
    return out


def main() -> None:
    src = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    turns = _load(src)
    flagged = 0
    for t in turns:
        f = flags(t)
        flagged += bool(f)
        tools = ", ".join(c["name"].split("__")[-1] for c in t.get("tools", []))
        print(f"{t['t'][5:16].replace('T', ' ')}  {t.get('outcome', '?'):9s} {t.get('duration') or 0:5.1f}s  "
              f"{(t.get('heard') or '(niets)')[:80]}")
        if tools:
            print(f"    tools: {tools}")
        for s in t.get("spoken", []):
            print(f"    zegt @{s['at']:.0f}s: {s['text'][:110]}")
        for x in f:
            print(f"    ⚑ {x}")
    print(f"\n{len(turns)} beurten, {flagged} met signalen")


if __name__ == "__main__":
    main()
