"""Voice pipeline over the ESPHome native API.

Ties the whole flow together on the reSpeaker:

    wake (on-device) → mic audio (API_AUDIO) → STT → agent loop → TTS → speaker

The device streams 16 kHz/16-bit/mono PCM to us; when it signals end-of-speech we
run STT → the agent (with home context + per-conversation history) → stream the TTS
reply back with `send_voice_assistant_audio`. Device LEDs/display are driven by
`send_voice_assistant_event` (RUN/STT/INTENT/TTS START/END).

OPT-IN (`settings.voice_enabled`): only ONE client may be the device's voice handler.
Home Assistant owns it while the device is adopted there — stop HA's pipeline for
this device before enabling this, or they fight.
"""

from __future__ import annotations

import logging
from typing import Any

from aioesphomeapi import APIClient, VoiceAssistantEventType

from .agent import AgentLoop, ConversationStore
from .config import Settings
from .home_context import HomeContext
from .stt import STTClient
from .trace import TraceBus
from .tts import TTSClient

_LOGGER = logging.getLogger(__name__)
_EVT = VoiceAssistantEventType
DEVICE_MIC_RATE = 16000  # ESPHome voice assistant mic stream


class VoicePipeline:
    def __init__(
        self,
        settings: Settings,
        trace: TraceBus,
        stt: STTClient,
        agent: AgentLoop,
        tts: TTSClient,
        convos: ConversationStore,
        home_ctx: HomeContext,
    ) -> None:
        self._s = settings
        self._trace = trace
        self._stt = stt
        self._agent = agent
        self._tts = tts
        self._convos = convos
        self._home = home_ctx
        self._cli: APIClient | None = None
        self._unsub: Any = None
        self._buf = bytearray()
        self._conv_id = "voice"
        self.attached = False  # subscribed as the device's voice handler
        self.last_activity: str = ""  # last pipeline stage, for the UI

    def status(self) -> dict[str, Any]:
        return {"enabled": self._s.voice_enabled, "attached": self.attached, "last": self.last_activity}

    def attach(self, cli: APIClient) -> None:
        """Subscribe as the device's voice handler. Called on each (re)connect."""
        self.detach()
        self._cli = cli
        self._unsub = cli.subscribe_voice_assistant(
            handle_start=self._on_start,
            handle_stop=self._on_stop,
            handle_audio=self._on_audio,
        )
        self.attached = True
        self._trace.emit("info", "voice pipeline attached to device")

    def detach(self) -> None:
        self.attached = False
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:  # noqa: BLE001 - connection may already be gone
                pass
            self._unsub = None

    # ── device callbacks ──────────────────────────────────────────────────────

    async def _on_start(self, conversation_id: str, flags: int, audio_settings: Any, wake_word_phrase: str | None) -> int:
        self._buf = bytearray()
        self._conv_id = conversation_id or "voice"
        self.last_activity = "luistert…"
        self._trace.emit("wake", wake_word_phrase or "(wake word)", data={"conversation_id": self._conv_id})
        self._event(_EVT.VOICE_ASSISTANT_RUN_START)
        return 0  # API audio: audio arrives via handle_audio, no separate port

    async def _on_audio(self, data: bytes, data2: bytes | None) -> None:
        self._buf.extend(data)

    async def _on_stop(self, aborted: bool) -> None:
        if aborted:
            self._buf = bytearray()
            return
        audio = bytes(self._buf)
        self._buf = bytearray()
        if not audio:
            self._event(_EVT.VOICE_ASSISTANT_RUN_END)
            return
        try:
            await self._handle_turn(audio)
        except Exception as err:  # noqa: BLE001 - never leave the device hanging
            self._trace.emit("error", f"voice turn failed: {err}", level="error")
            self._event(_EVT.VOICE_ASSISTANT_ERROR, {"code": "pipeline_error", "message": str(err)[:120]})
        finally:
            self._event(_EVT.VOICE_ASSISTANT_RUN_END)

    # ── the turn ────────────────────────────────────────────────────────────────

    async def _handle_turn(self, audio: bytes) -> None:
        self.last_activity = "transcriberen…"
        self._event(_EVT.VOICE_ASSISTANT_STT_START)
        text = await self._stt.transcribe(audio, in_rate=DEVICE_MIC_RATE)
        self._event(_EVT.VOICE_ASSISTANT_STT_END, {"text": text})
        if not text:
            self.last_activity = "niets verstaan"
            return

        self.last_activity = "denken…"
        self._event(_EVT.VOICE_ASSISTANT_INTENT_START)
        result = await self._agent.run(text, history=self._convos.get(self._conv_id), context=self._home.get())
        self._convos.update(self._conv_id, result.messages)
        self._event(_EVT.VOICE_ASSISTANT_INTENT_END)
        if not result.text:
            return

        self.last_activity = "praten…"
        self._event(_EVT.VOICE_ASSISTANT_TTS_START, {"text": result.text})
        assert self._cli is not None
        async for chunk in self._tts.synth_stream(result.text):
            self._cli.send_voice_assistant_audio(chunk)
        self._event(_EVT.VOICE_ASSISTANT_TTS_END)
        self.last_activity = "klaar"

    def _event(self, event_type: VoiceAssistantEventType, data: dict[str, str] | None = None) -> None:
        if self._cli is not None:
            try:
                self._cli.send_voice_assistant_event(event_type, data)
            except Exception:  # noqa: BLE001
                pass
