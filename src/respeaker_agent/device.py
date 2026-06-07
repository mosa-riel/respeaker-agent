"""DeviceLink — maintains a resilient connection to the reSpeaker over the ESPHome
native API and tracks live status for the UI. Phase 1: connect, read device info,
list entities, log state changes. (Voice flow comes in phase 2.)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from aioesphomeapi import APIClient, ReconnectLogic
from zeroconf.asyncio import AsyncZeroconf

from .config import Secrets, Settings
from .trace import TraceBus

_LOGGER = logging.getLogger(__name__)


class DeviceLink:
    def __init__(self, settings: Settings, secrets: Secrets, trace: TraceBus) -> None:
        self._settings = settings
        self._secrets = secrets
        self._trace = trace
        self._cli: APIClient | None = None
        self._reconnect: ReconnectLogic | None = None
        self._aiozc: AsyncZeroconf | None = None
        # Optional hook run with the connected client after each (re)connect — used
        # by the voice pipeline to (re)subscribe as the device's voice handler.
        self.post_connect: Callable[[APIClient], None] | None = None

        self.connected: bool = False
        self.device_info: dict[str, Any] = {}
        self.entities: list[dict[str, str]] = []
        self._names: dict[int, str] = {}
        self.last_error: str | None = None

    async def start(self) -> None:
        self._aiozc = AsyncZeroconf()
        self._cli = APIClient(
            address=self._settings.device_host,
            port=self._settings.device_port,
            password=self._secrets.device_password,
            noise_psk=self._secrets.device_noise_psk,
        )
        self._reconnect = ReconnectLogic(
            client=self._cli,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            zeroconf_instance=self._aiozc.zeroconf,
            name=self._settings.device_host,
        )
        await self._reconnect.start()

    async def stop(self) -> None:
        if self._reconnect:
            await self._reconnect.stop()
        if self._cli:
            await self._cli.disconnect()
        if self._aiozc:
            await self._aiozc.async_close()

    async def _on_connect(self) -> None:
        assert self._cli is not None
        try:
            info = await self._cli.device_info()
            self.device_info = {
                "name": info.name,
                "model": info.model,
                "esphome_version": info.esphome_version,
                "mac": info.mac_address,
                "project": f"{info.project_name} {info.project_version}".strip(),
            }
            entities, _services = await self._cli.list_entities_services()
            self._names = {e.key: (e.name or e.object_id) for e in entities}
            self.entities = [
                {"name": e.name or e.object_id, "type": type(e).__name__.replace("Info", "")}
                for e in entities
            ]
            self._cli.subscribe_states(self._on_state)
            self.connected = True
            self.last_error = None
            self._trace.emit(
                "device",
                f"connected to {self.device_info.get('name')} ({len(self.entities)} entities)",
                data=self.device_info,
            )
            if self.post_connect is not None:
                self.post_connect(self._cli)
        except Exception as err:  # noqa: BLE001 - surface to UI
            self.last_error = _safe_error(err)
            self._trace.emit("error", f"device on_connect failed: {self.last_error}", level="error")
            _LOGGER.exception("on_connect failed")

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        self.connected = False
        kind = "expected" if expected_disconnect else "unexpected"
        self._trace.emit("device", f"disconnected ({kind})", level="info" if expected_disconnect else "warn")

    def _on_state(self, state: Any) -> None:
        name = self._names.get(getattr(state, "key", -1), "?")
        value = getattr(state, "state", None)
        self._trace.emit("device", f"{name} = {value}", direction="in")

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "host": self._settings.device_host,
            "port": self._settings.device_port,
            "device_info": self.device_info,
            "entity_count": len(self.entities),
            "entities": self.entities,
            "last_error": self.last_error,
        }


def _safe_error(err: Exception) -> str:
    """Trim error text so internal/host detail isn't reflected verbatim to the UI."""
    msg = str(err) or err.__class__.__name__
    return msg.splitlines()[0][:200]
