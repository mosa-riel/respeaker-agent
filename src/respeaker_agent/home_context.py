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
            attrs = await self._attributes(exposed)  # entity_id -> state attributes (existing only)
            names = {eid: a.get("friendly_name") or eid for eid, a in attrs.items()}
            areas, floor_of = await self._area_names()   # area_id -> name, room name -> (level, floor)
            ent2area = await self._entity_to_area()       # entity_id -> area_id (via device)
            registry = await self._entity_registry(exposed)  # entity_id -> own area + aliases
        except Exception as err:  # noqa: BLE001
            self._trace.emit("error", f"home context refresh failed: {err}", level="error")
            return self.entity_count

        # Exposure settings outlive deleted entities. Drop ids HA has no state for: a
        # service call on one waits ~10 s for a state change that never comes.
        ghosts = sorted(exposed - names.keys()) if names else []
        if ghosts:
            exposed = exposed - set(ghosts)
            self._trace.emit("info", f"home context: skipped {len(ghosts)} exposed entities that no longer exist",
                             data={"entity_ids": ghosts})

        rooms: dict[str, list[str]] = {}
        for eid in exposed:
            dom = eid.split(".", 1)[0]
            label = DOMAIN_TYPE.get(dom, dom)
            entry = registry.get(eid, {})
            # The entity's own area wins over its device's, as in HA: one bridge device
            # (Somfy RTS, the Zigbee coordinator for ZHA groups) carries entities that
            # live in other rooms.
            room = areas.get(entry.get("area_id") or ent2area.get(eid, ""), NO_ROOM)
            aliases = [a for a in entry.get("aliases") or [] if a]
            also = f" (ook: {'; '.join(aliases)})" if aliases else ""
            # Include the entity_id so the model can call tools directly — no search
            # round, no guessing a (wrong) id.
            controls = _controls(eid, attrs.get(eid, {}))
            rooms.setdefault(room, []).append(f"{names.get(eid, eid)} [{eid}] ({label}{controls}){also}")
        if not rooms:
            return self.entity_count

        # Floors first, so "beneden"/"boven" map to real rooms instead of a guess.
        floors: dict[tuple[int, str], list[str]] = {}
        for room in rooms:
            if room in floor_of:
                floors.setdefault(floor_of[room], []).append(room)
        floor_lines = [f"{name} (niveau {level}): {', '.join(sorted(rs))}" for (level, name), rs in sorted(floors.items())]
        # Stable order: named rooms first (alpha), "Zonder ruimte" last.
        lines = []
        for room in sorted(rooms, key=lambda r: (r == NO_ROOM, r.lower())):
            names = ", ".join(sorted(set(rooms[room])))
            lines.append(f"{room}: {names}")
        self._text = ""
        if floor_lines:
            self._text = "Verdiepingen:\n" + "\n".join(floor_lines) + "\n\n"
        self._text += "Bekende apparaten per ruimte (gebruik exact deze namen):\n" + "\n".join(lines)
        self._ts = time.time()
        self.entity_count = sum(len(v) for v in rooms.values())
        self._trace.emit("info", f"home context refreshed ({self.entity_count} entities, {len(rooms)} rooms)")
        return self.entity_count

    # ── data sources ────────────────────────────────────────────────────────

    def _connected(self) -> bool:
        st = getattr(self._mgr, "status", {}).get(self._server, {})
        return bool(st.get("connected"))

    async def _area_names(self) -> tuple[dict[str, str], dict[str, tuple[int, str]]]:
        """area_id -> name, and area name -> (floor level, floor name) for areas on a floor."""
        data = _data(await self._mgr.call_raw(self._server, "ha_list_floors_areas", {}))
        out: dict[str, str] = {}
        floor_of: dict[str, tuple[int, str]] = {}
        # areas appear under unassigned_areas and/or floors[].areas
        for a in data.get("unassigned_areas", []):
            if a.get("area_id"):
                out[a["area_id"]] = a.get("name") or a["area_id"]
        for fl in data.get("floors", []):
            for a in fl.get("areas", []):
                if a.get("area_id"):
                    name = a.get("name") or a["area_id"]
                    out[a["area_id"]] = name
                    floor_of[name] = (int(fl.get("level") or 0), fl.get("name") or fl.get("floor_id", ""))
        return out, floor_of

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

    async def _entity_registry(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        """entity_id -> entity registry entry (`area_id` override, `aliases`), in batches."""
        out: dict[str, dict[str, Any]] = {}
        todo = sorted(ids)
        for i in range(0, len(todo), 50):
            data = _data(await self._mgr.call_raw(self._server, "ha_get_entity", {"entity_id": todo[i:i + 50]}))
            for entry in data.get("entity_entries", []) or []:
                if entry.get("entity_id"):
                    out[entry["entity_id"]] = entry
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

    async def _attributes(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        """entity_id -> state attributes (friendly name, supported features), read live
        from HA for the exposed set; ids HA has no state for are absent."""
        if not ids:
            return {}
        data = _data(await self._mgr.call_raw(self._server, "ha_get_state", {"entity_id": sorted(ids)}))
        states = data.get("states", data) if isinstance(data, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        if isinstance(states, dict):
            for eid, st in states.items():
                out[eid] = ((st or {}).get("attributes") or {}) if isinstance(st, dict) else {}
        return out


# HA CoverEntityFeature bits for tilting.
_COVER_TILT = 16 | 32 | 128  # OPEN_TILT | CLOSE_TILT | SET_TILT_POSITION
_COVER_SET_TILT = 128


def _controls(eid: str, attrs: dict[str, Any]) -> str:
    """How to operate the device, from HA's own feature flags, so the model reads it off
    the controls instead of a house rule. A cover that tilts (venetian blinds): "open/dicht"
    means tilting, "omhoog/omlaag" means position — "lamellen open" used to raise them."""
    if not eid.startswith("cover."):
        return ""
    features = int(attrs.get("supported_features") or 0)
    if not features & _COVER_TILT:
        return ""
    text = "; omhoog/omlaag = open_cover/close_cover, open/dicht = kantelen met open_cover_tilt/close_cover_tilt"
    if features & _COVER_SET_TILT:
        text += ", kanteling in % met set_cover_tilt_position"
    return text


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
