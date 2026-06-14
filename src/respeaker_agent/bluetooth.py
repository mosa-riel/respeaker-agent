"""Bluetooth control — a tightly-scoped wrapper around the host `bluetoothctl`.

Lets the agent (voice or web UI) scan for, pair, connect and disconnect a Bluetooth
A2DP speaker on the HOST, so TTS can play out it (see `local_play` + `audio_sink`).

SECURITY — this runs a host binary, so it is fenced in:
  * OPT-IN behind `settings.bluetooth_control` (off by default); tools aren't even
    registered otherwise.
  * The binary is fixed (`bluetoothctl`) and spawned with an argv list — never a
    shell, never a user-supplied command/args (the project's hard gate).
  * The sub-command is allowlisted here; callers can't pick arbitrary ones.
  * The only free argument is a MAC address, validated against a strict regex and
    re-emitted in canonical form. A value that fails the regex is rejected before
    any process spawns.
On a successful connect we derive + set the matching PulseAudio sink name on
`settings.audio_sink` (in-memory) so TTS routes to the new speaker immediately;
persist it via the config API to survive a restart.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .config import Settings
from .tools import Tool
from .trace import TraceBus

_MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")  # canonical: uppercase, colon-separated
_TIMEOUT = 30.0
_SCAN_SECS = 12


def _norm_mac(mac: Any) -> str | None:
    """Canonicalise + validate a MAC. None if it isn't a real MAC (reject, don't run)."""
    if not isinstance(mac, str):
        return None
    m = mac.strip().upper().replace("-", ":")
    return m if _MAC.match(m) else None


def sink_for(mac: str) -> str:
    """PulseAudio A2DP sink name for a connected speaker MAC."""
    return f"bluez_sink.{mac.replace(':', '_')}.a2dp_sink"


async def _ctl(args: list[str], timeout: float = _TIMEOUT) -> dict[str, Any]:
    """Run `bluetoothctl <args>` with no shell. `args` is fixed + pre-validated."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "bluetoothctl not found on host"}
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": f"bluetoothctl {' '.join(args)} timed out"}
    text = (out or b"").decode(errors="replace").strip()
    return {"ok": proc.returncode == 0, "rc": proc.returncode, "output": text[-1500:]}


def _parse_devices(output: str) -> list[dict[str, str]]:
    """`Device AA:BB:.. Name` lines → [{mac, name}]."""
    devs = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2 and parts[0] == "Device":
            mac = _norm_mac(parts[1])
            if mac:
                devs.append({"mac": mac, "name": parts[2] if len(parts) > 2 else ""})
    return devs


def bluetooth_tools(settings: Settings, trace: TraceBus) -> list[Tool]:
    """Build the locked-down Bluetooth agent tools (empty unless control is on)."""
    if not settings.bluetooth_control:
        return []

    async def _list(_: dict[str, Any]) -> dict[str, Any]:
        known = await _ctl(["devices"])
        conn = await _ctl(["devices", "Connected"])
        if not known.get("ok"):
            return {"error": known.get("error") or known.get("output", "bluetoothctl failed")}
        connected = {d["mac"] for d in _parse_devices(conn.get("output", ""))}
        devs = [{**d, "connected": d["mac"] in connected} for d in _parse_devices(known.get("output", ""))]
        return {"devices": devs, "audio_sink": settings.audio_sink}

    async def _scan(_: dict[str, Any]) -> dict[str, Any]:
        trace.emit("tool", f"bluetooth scan {_SCAN_SECS}s", direction="out")
        await _ctl(["--timeout", str(_SCAN_SECS), "scan", "on"], timeout=_SCAN_SECS + 10)
        listing = await _ctl(["devices"])
        return {"discovered": _parse_devices(listing.get("output", ""))}

    async def _connect(args: dict[str, Any]) -> dict[str, Any]:
        mac = _norm_mac(args.get("mac"))
        if not mac:
            return {"error": "invalid MAC address (want AA:BB:CC:DD:EE:FF)"}
        trace.emit("tool", f"bluetooth connect {mac}", direction="out", data={"mac": mac})
        steps: dict[str, Any] = {}
        # pair is a no-op / harmless error if already paired; trust then connect.
        steps["pair"] = await _ctl(["pair", mac])
        steps["trust"] = await _ctl(["trust", mac])
        conn = await _ctl(["connect", mac])
        steps["connect"] = conn
        info = await _ctl(["info", mac])
        ok = conn.get("ok") and "Connected: yes" in info.get("output", "")
        sink = sink_for(mac)
        if ok:
            settings.audio_sink = sink  # route TTS here now (persist via config API to keep)
            trace.emit("info", f"audio_sink → {sink} (in-memory; save config to persist)")
        return {
            "ok": bool(ok),
            "mac": mac,
            "audio_sink": sink if ok else settings.audio_sink,
            "note": "Connected; TTS now routes to this speaker. Save config to keep it after restart."
                    if ok else "Connect failed — see steps.",
            "steps": {k: v.get("output") or v.get("error") for k, v in steps.items()},
        }

    async def _disconnect(args: dict[str, Any]) -> dict[str, Any]:
        mac = _norm_mac(args.get("mac"))
        if not mac:
            return {"error": "invalid MAC address (want AA:BB:CC:DD:EE:FF)"}
        trace.emit("tool", f"bluetooth disconnect {mac}", direction="out", data={"mac": mac})
        res = await _ctl(["disconnect", mac])
        return {"ok": res.get("ok", False), "mac": mac, "output": res.get("output") or res.get("error")}

    _mac_param = {
        "type": "object",
        "properties": {"mac": {"type": "string", "description": "Speaker MAC, e.g. AA:BB:CC:DD:EE:FF"}},
        "required": ["mac"],
    }
    _no_param = {"type": "object", "properties": {}, "required": []}
    return [
        Tool("bluetooth_list", "List known Bluetooth devices on the host and which are connected. Use to find a speaker's MAC.", _no_param, _list),
        Tool("bluetooth_scan", f"Scan ~{_SCAN_SECS}s for nearby Bluetooth devices and list them with their MAC addresses.", _no_param, _scan),
        Tool("bluetooth_connect", "Pair (if needed), trust and connect a Bluetooth speaker by MAC, then route TTS audio to it.", _mac_param, _connect),
        Tool("bluetooth_disconnect", "Disconnect a Bluetooth speaker by MAC.", _mac_param, _disconnect),
    ]
