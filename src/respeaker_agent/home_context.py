"""HomeContext — the agent's ground truth about what exists in the home, grouped
by real room.

Without this the model guesses (invents a "plafondlicht", miscounts, fabricates
areas). Room can't be inferred from entity names reliably, so we join the real
data from HA: controllable entities (friendly names) × the device registry
(entity → device → area_id) × the area registry (area_id → name). The result is a
compact "per room: these devices" list injected into the system prompt.

Identity only (names + room), NOT current state — state changes between refreshes,
so the model must read it live with a tool (the prompt enforces this). Refreshed
periodically and on demand ("vernieuw apparaten").
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .trace import TraceBus

_LOGGER = logging.getLogger(__name__)

# WHICH entities/domains to ground is NOT hardcoded — it's whatever the user exposed
# to Assist in HA (the source of truth). This map only provides a nicer Dutch label
# per domain for readability; any unlisted domain falls back to its raw name.
DOMAIN_TYPE = {
    "light": "lamp",
    "switch": "schakelaar",
    "cover": "rolluik/scherm",
    "climate": "thermostaat",
    "fan": "ventilator",
    "scene": "scène",
    "script": "script",
    "media_player": "mediaspeler",
    "lock": "slot",
    "vacuum": "stofzuiger",
}
NO_ROOM = "Zonder ruimte"


class HomeContext:
    def __init__(self, manager: Any, trace: TraceBus, server: str = "home-assistant") -> None:
        self._mgr = manager
        self._trace = trace
        self._server = server
        self._text: str = ""
        self._ts: float = 0.0
        self.entity_count: int = 0

    def get(self) -> str | None:
        return self._text or None

    async def refresh(self) -> int:
        """Rebuild the cached, room-grouped entity list. Best-effort: a failure
        leaves the previous cache in place. Returns the entity count."""
        if not self._connected():
            return self.entity_count
        try:
            exposed = await self._exposed()            # entity_ids the user exposed to Assist
            if not exposed:
                self._trace.emit("info", "home context: no entities exposed to Assist (nothing to ground)")
                return self.entity_count
            names = await self._friendly_names(exposed)  # entity_id -> friendly name
            areas = await self._area_names()             # area_id -> name
            ent2area = await self._entity_to_area()       # entity_id -> area_id (via device)
        except Exception as err:  # noqa: BLE001
            self._trace.emit("error", f"home context refresh failed: {err}", level="error")
            return self.entity_count

        rooms: dict[str, list[str]] = {}
        for eid in exposed:
            dom = eid.split(".", 1)[0]
            label = DOMAIN_TYPE.get(dom, dom)
            room = areas.get(ent2area.get(eid, ""), NO_ROOM)
            # Include the entity_id so the model can call tools directly — no search
            # round, no guessing a (wrong) id.
            rooms.setdefault(room, []).append(f"{names.get(eid, eid)} [{eid}] ({label})")
        if not rooms:
            return self.entity_count

        # Stable order: named rooms first (alpha), "Zonder ruimte" last.
        lines = []
        for room in sorted(rooms, key=lambda r: (r == NO_ROOM, r.lower())):
            names = ", ".join(sorted(set(rooms[room])))
            lines.append(f"{room}: {names}")
        self._text = "Bekende apparaten per ruimte (gebruik exact deze namen):\n" + "\n".join(lines)
        self._ts = time.time()
        self.entity_count = sum(len(v) for v in rooms.values())
        self._trace.emit("info", f"home context refreshed ({self.entity_count} entities, {len(rooms)} rooms)")
        return self.entity_count

    # ── data sources ────────────────────────────────────────────────────────

    def _connected(self) -> bool:
        st = getattr(self._mgr, "status", {}).get(self._server, {})
        return bool(st.get("connected"))

    async def _area_names(self) -> dict[str, str]:
        data = _data(await self._mgr.call_raw(self._server, "ha_list_floors_areas", {}))
        out: dict[str, str] = {}
        # areas appear under unassigned_areas and/or floors[].areas
        buckets = list(data.get("unassigned_areas", []))
        for fl in data.get("floors", []):
            buckets.extend(fl.get("areas", []))
        for a in buckets:
            if a.get("area_id"):
                out[a["area_id"]] = a.get("name") or a["area_id"]
        return out

    async def _entity_to_area(self) -> dict[str, str]:
        """entity_id -> area_id, resolved through the device registry (entities
        inherit their device's area)."""
        out: dict[str, str] = {}
        offset = 0
        for _ in range(20):  # pagination guard
            # detail_level="full" is required to get each device's `entities` list
            # (the default omits it, which would leave every entity room-less).
            data = _data(await self._mgr.call_raw(self._server, "ha_get_device", {"offset": offset, "limit": 100, "detail_level": "full"}))
            devices = data.get("devices", [])
            for d in devices:
                area = d.get("area_id")
                if not area:
                    continue
                for ent in d.get("entities", []) or []:
                    eid = ent.get("entity_id")
                    if eid:
                        out[eid] = area
            if not data.get("has_more"):
                break
            offset = data.get("next_offset") or (offset + len(devices))
        return out

    async def _exposed(self) -> set[str]:
        """Entity ids exposed to Assist (conversation). Empty set if unavailable —
        the caller then falls back to all control entities."""
        try:
            data = _data(await self._mgr.call_raw(self._server, "ha_get_entity_exposure", {}))
        except Exception:  # noqa: BLE001
            return set()
        exposed = data.get("exposed_entities") or {}
        if isinstance(exposed, dict):
            return {eid for eid, v in exposed.items() if (v or {}).get("conversation")}
        return set()

    async def _friendly_names(self, ids: set[str]) -> dict[str, str]:
        """entity_id -> friendly name, read live from HA state for the exposed set."""
        if not ids:
            return {}
        data = _data(await self._mgr.call_raw(self._server, "ha_get_state", {"entity_id": sorted(ids)}))
        states = data.get("states", data) if isinstance(data, dict) else {}
        out: dict[str, str] = {}
        if isinstance(states, dict):
            for eid, st in states.items():
                attrs = (st or {}).get("attributes", {}) if isinstance(st, dict) else {}
                out[eid] = attrs.get("friendly_name") or eid
        return out


def _data(res: Any) -> dict[str, Any]:
    """Pull the payload dict out of a HomeContext call_raw result (structured first,
    then the JSON `content` string). ha-mcp wraps some results in a {"data": ...}."""
    obj: Any = None
    if isinstance(res, dict):
        struct = res.get("structured")
        if isinstance(struct, dict):
            obj = struct
        else:
            try:
                obj = json.loads(res.get("content", "") or "{}")
            except (json.JSONDecodeError, TypeError):
                obj = {}
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
        return obj["data"]
    return obj if isinstance(obj, dict) else {}
