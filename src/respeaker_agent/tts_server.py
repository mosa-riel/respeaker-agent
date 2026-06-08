"""Tiny LAN-reachable HTTP server that serves ONLY TTS audio clips.

This firmware plays TTS by having its media_player fetch a URL (it logs "No url in
TTS_END event" otherwise) — the API-audio stream path isn't wired to a speaker. So
we publish each reply as a WAV and hand the device a URL to fetch.

The device can't reach the agent on 127.0.0.1, so this binds the LAN interface. It's
deliberately separate from the config API and serves audio ONLY: GET /<token>.wav
returns an ephemeral, random-token clip; everything else 404s. No secrets, no
state-changing routes. See docs/reference/security.md.
"""

from __future__ import annotations

import logging
import socket
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOGGER = logging.getLogger(__name__)


class TTSAudioServer:
    def __init__(self, port: int, max_clips: int = 8) -> None:
        self._port = port
        self._max = max_clips
        # filename ("<token>.<ext>") -> (bytes, content_type)
        self._clips: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self.lan_ip = "127.0.0.1"

    def publish(self, name: str, data: bytes, content_type: str) -> None:
        with self._lock:
            self._clips[name] = (data, content_type)
            while len(self._clips) > self._max:
                self._clips.popitem(last=False)

    def _get(self, name: str) -> tuple[bytes, str] | None:
        with self._lock:
            return self._clips.get(name)

    def url_for(self, name: str) -> str:
        return f"http://{self.lan_ip}:{self._port}/{name}"

    def start(self, device_host: str, advertise_host: str | None = None) -> None:
        # When the agent is bridged (HA add-on), its own IP isn't reachable by the device
        # — caller passes the host's LAN IP to advertise. Otherwise auto-detect.
        self.lan_ip = advertise_host or _detect_lan_ip(device_host)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence default stderr logging
                pass

            def do_GET(self):  # noqa: N802
                clip = server._get(self.path.lstrip("/"))
                if clip is None:
                    self.send_error(404)
                    return
                data, content_type = clip
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        _LOGGER.info("TTS audio server on http://%s:%s (audio only)", self.lan_ip, self._port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None


def _detect_lan_ip(device_host: str) -> str:
    """Local IP on the interface that reaches the device."""
    try:
        ip = device_host if _is_ipv4(device_host) else socket.gethostbyname(device_host)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((ip, 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _is_ipv4(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)
