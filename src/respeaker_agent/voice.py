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

import asyncio
import logging
import math
import uuid
from typing import Any

import numpy as np
from aioesphomeapi import APIClient, VoiceAssistantEventType

from .audio import int16_to_wav_bytes
from .agent import AgentLoop, ConversationStore
from .config import Settings
from .home_context import HomeContext
from .stt import STTClient
from .trace import TraceBus
from .tts import TTSClient
from .tts_server import TTSAudioServer

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
        audio_srv: TTSAudioServer,
    ) -> None:
        self._s = settings
        self._trace = trace
        self._stt = stt
        self._agent = agent
        self._tts = tts
        self._convos = convos
        self._home = home_ctx
        self._audio_srv = audio_srv
        self._cli: APIClient | None = None
        self._unsub: Any = None
        self._buf = bytearray()
        self._conv_id = "voice"
        # server-side VAD state (reset per utterance)
        self._finalizing = False
        self._speech = False
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self.attached = False  # subscribed as the device's voice handler
        self.last_activity: str = ""  # last pipeline stage, for the UI

    def status(self) -> dict[str, Any]:
        return {"enabled": self._s.voice_enabled, "attached": self.attached, "last": self.last_activity}

    def attach(self, cli: APIClient) -> None:
        """Subscribe as the device's voice handler. Called on each (re)connect.

        On a reconnect the previous subscription died with the old connection — we
        must NOT call the old unsub (it would send an `unsubscribe` on the NEW
        connection and fight our fresh subscribe; the device logs "Client attempting
        to unsubscribe that is not the current API Client"). Just drop the stale ref.
        """
        self._unsub = None
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
        self._finalizing = False
        self._speech = False
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self.last_activity = "luistert…"
        self._trace.emit("wake", wake_word_phrase or "(wake word)", data={"conversation_id": self._conv_id, "flags": flags})
        self._event(_EVT.VOICE_ASSISTANT_RUN_START)
        return 0  # API audio: audio arrives via handle_audio, no separate port

    async def _on_audio(self, data: bytes, data2: bytes | None) -> None:
        # This firmware streams continuously and never sends audio.end — WE decide
        # end-of-speech (energy VAD), then finalize.
        if self._finalizing or not data:
            return
        if not self._buf:
            self._trace.emit("info", "voice: receiving audio…")
        self._buf.extend(data)

        samples = np.frombuffer(data, dtype="<i2")
        if samples.size == 0:
            return
        rms = math.sqrt(float(np.mean(samples.astype(np.float32) ** 2)))
        dur_ms = samples.size / 16.0  # 16000 samples/s → samples/16 = ms
        self._heard_ms += dur_ms
        if rms > self._s.vad_threshold:
            if not self._speech:
                self._speech = True
                self.last_activity = "opname…"
                self._event(_EVT.VOICE_ASSISTANT_STT_VAD_START)
            self._silence_ms = 0.0
        elif self._speech:
            self._silence_ms += dur_ms

        end_of_speech = self._speech and self._silence_ms >= self._s.vad_silence_ms
        too_long = self._heard_ms >= self._s.vad_max_ms
        no_speech = not self._speech and self._heard_ms >= self._s.vad_prespeech_ms
        if end_of_speech or too_long or no_speech:
            self._finalize(spoke=self._speech and not no_speech)

    async def _on_stop(self, aborted: bool) -> None:
        # Device-driven stop (abort, or a firmware that DOES send end). Process
        # whatever we have unless already finalizing.
        if aborted:
            self._reset()
            self._event(_EVT.VOICE_ASSISTANT_RUN_END)
            return
        self._finalize(spoke=self._speech)

    def _finalize(self, *, spoke: bool) -> None:
        if self._finalizing:
            return
        self._finalizing = True
        if self._speech:
            self._event(_EVT.VOICE_ASSISTANT_STT_VAD_END)
        audio = bytes(self._buf)
        self._buf = bytearray()
        asyncio.create_task(self._finish(audio, spoke))

    async def _finish(self, audio: bytes, spoke: bool) -> None:
        try:
            if spoke and audio:
                await self._handle_turn(audio)
            else:
                self._trace.emit("info", "voice: no speech captured")
        except Exception as err:  # noqa: BLE001 - never leave the device hanging
            self._trace.emit("error", f"voice turn failed: {err}", level="error")
            self._event(_EVT.VOICE_ASSISTANT_ERROR, {"code": "pipeline_error", "message": str(err)[:120]})
        finally:
            self._event(_EVT.VOICE_ASSISTANT_RUN_END)
            self._reset()

    def _reset(self) -> None:
        self._buf = bytearray()
        self._finalizing = False
        self._speech = False
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self.last_activity = "klaar"

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
        assert self._cli is not None
        self._event(_EVT.VOICE_ASSISTANT_TTS_START, {"text": result.text})
        # This firmware plays TTS via its media_player fetching a URL ("No url in
        # TTS_END event" otherwise). Synthesize the full reply, publish it as a WAV
        # on the LAN audio server, and hand the device the URL. The device resamples
        # (its media pipeline is 48 kHz) — we serve 16 kHz mono WAV.
        pcm = await self._tts.synth(result.text)
        wav = int16_to_wav_bytes(np.frombuffer(pcm, dtype="<i2"), self._s.tts_out_rate)
        token = uuid.uuid4().hex
        self._audio_srv.publish(token, wav)
        url = self._audio_srv.url_for(token)
        self._trace.emit("tts", f"serving reply ({len(wav)} B) at {url}", direction="out")
        self._event(_EVT.VOICE_ASSISTANT_TTS_END, {"url": url})
        self.last_activity = "klaar"

    def _event(self, event_type: VoiceAssistantEventType, data: dict[str, str] | None = None) -> None:
        if self._cli is not None:
            try:
                self._cli.send_voice_assistant_event(event_type, data)
            except Exception:  # noqa: BLE001
                pass
