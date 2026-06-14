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

from .audio import flac_duration_seconds
from .local_play import play_pcm_stream
from .agent import AgentLoop, ConversationStore
from .config import Settings
from .stt import STTClient
from .trace import TraceBus
from .tts import TTSClient
from .tts_server import TTSAudioServer

_LOGGER = logging.getLogger(__name__)
_EVT = VoiceAssistantEventType
DEVICE_MIC_RATE = 16000  # ESPHome voice assistant mic stream
_CONTENT_TYPES = {"flac": "audio/flac", "mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}


class VoicePipeline:
    def __init__(
        self,
        settings: Settings,
        trace: TraceBus,
        stt: STTClient,
        agent: AgentLoop,
        tts: TTSClient,
        convos: ConversationStore,
        mcp: Any,
        audio_srv: TTSAudioServer,
    ) -> None:
        self._s = settings
        self._trace = trace
        self._stt = stt
        self._agent = agent
        self._tts = tts
        self._convos = convos
        self._mcp = mcp  # McpManager — for the per-source tool summary
        self._audio_srv = audio_srv
        self._cli: APIClient | None = None
        self._unsub: Any = None
        self._buf = bytearray()
        self._conv_id = "voice"
        # server-side VAD state (reset per utterance)
        self._finalizing = False
        self._ended = False
        self._speech = False
        self._speech_byte = 0
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self._conversing = False  # in an active follow-up session
        self._chime_url: str | None = None
        self._end_chime_path: str | None = None  # cached end-chime flac for the local sink
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
        self._ended = False
        self._speech = False
        self._speech_byte = 0
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self.last_activity = "luistert…"
        if wake_word_phrase:  # a real wake word starts a fresh session (not a follow-up re-listen)
            self._conversing = False
        self._trace.emit("wake", wake_word_phrase or "(vervolg)", data={"conversation_id": self._conv_id, "flags": flags})
        if self._s.wake_chime and self._s.audio_sink:
            asyncio.create_task(self._play_wake_chime())  # cue on the BT/host speaker; don't block the mic
        self._event(_EVT.VOICE_ASSISTANT_RUN_START)
        # Enter the listening phase NOW (drives the device's listening LEDs) — the
        # transcript comes later in STT_END. (Sending STT_START after speech ended
        # would skip the listening animation entirely.)
        self._event(_EVT.VOICE_ASSISTANT_STT_START)
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
                # byte offset where speech began (this chunk) — used to trim leading
                # silence; clear initial silence helps Voxtral lock the language.
                self._speech_byte = max(0, len(self._buf) - len(data))
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
        # Trim leading silence to ~120ms before speech onset (16k/16-bit mono → 2 B/sample).
        start = max(0, self._speech_byte - 16000 * 2 * 120 // 1000) if self._speech else 0
        audio = bytes(self._buf[start:])
        self._buf = bytearray()
        asyncio.create_task(self._finish(audio, spoke))

    async def _finish(self, audio: bytes, spoke: bool) -> None:
        replied = False
        try:
            if spoke and audio:
                replied = await self._handle_turn(audio)
            else:
                self._trace.emit("info", "voice: no speech captured")
        except Exception as err:  # noqa: BLE001 - never leave the device hanging
            self._trace.emit("error", f"voice turn failed: {err}", level="error")
            self._event(_EVT.VOICE_ASSISTANT_ERROR, {"code": "pipeline_error", "message": str(err)[:120]})
        finally:
            # Ended without a reply (no speech / nothing to say) → session closes.
            # Chime so you hear the mic shut. (Only meaningful with follow-up on.)
            session_end = self._s.voice_followup and not replied
            self._run_end()
            if session_end and self._s.voice_end_chime:
                await self._play_chime()
            self._conversing = False
            self._reset()

    def _reset(self) -> None:
        self._buf = bytearray()
        self._finalizing = False
        self._speech = False
        self._silence_ms = 0.0
        self._heard_ms = 0.0
        self.last_activity = "klaar"

    # ── the turn ────────────────────────────────────────────────────────────────

    async def _handle_turn(self, audio: bytes) -> bool:
        """Run STT → agent → TTS. Returns True if it produced a spoken reply
        (in follow-up mode that also means the mic was re-opened)."""
        self.last_activity = "transcriberen…"
        self._event(_EVT.VOICE_ASSISTANT_STT_START)
        text = await self._stt.transcribe(audio, in_rate=DEVICE_MIC_RATE)
        self._event(_EVT.VOICE_ASSISTANT_STT_END, {"text": text})
        if self._s.save_recordings:
            from . import recordings
            recordings.save(audio, DEVICE_MIC_RATE, text)  # keep what STT heard, to play back
        if not text:
            self.last_activity = "niets verstaan"
            return False

        self.last_activity = "denken…"
        self._event(_EVT.VOICE_ASSISTANT_INTENT_START)
        result = await self._agent.run(text, history=self._convos.get(self._conv_id), context=self._mcp.sources_summary())
        self._convos.update(self._conv_id, result.messages)
        self._event(_EVT.VOICE_ASSISTANT_INTENT_END)
        if not result.text:
            return False

        self.last_activity = "praten…"
        assert self._cli is not None
        self._event(_EVT.VOICE_ASSISTANT_TTS_START, {"text": result.text})
        if self._s.audio_sink:
            # Local-sink mode: stream TTS straight to a host PulseAudio sink (e.g. a
            # paired Bluetooth speaker) instead of the device. We AWAIT real playback,
            # so no duration guessing. Follow-up (mic re-open) rides the device announce
            # path, which we skip here — so this turn ends and the user wakes again.
            # (synth_stream already emits the "tts" trace — don't double-log here.)
            await play_pcm_stream(self._tts.synth_stream(result.text), self._s.audio_sink, self._s.tts_out_rate, self._trace)
            self._event(_EVT.VOICE_ASSISTANT_TTS_END)
            self._run_end()
            self.last_activity = "klaar"
            return True
        # This firmware plays TTS via its media_player fetching a URL ("No url in
        # TTS_END event" otherwise) and decodes by type — its pipeline is FLAC and it
        # rejects WAV. Ask the engine for the configured format, publish it on the LAN
        # audio server, hand the device the URL; it resamples to its 48 kHz pipeline.
        fmt = self._s.tts_voice_format
        data = await self._tts.synth_encoded(result.text, fmt)
        name = f"{uuid.uuid4().hex}.{fmt}"
        self._audio_srv.publish(name, data, _CONTENT_TYPES.get(fmt, "application/octet-stream"))
        url = self._audio_srv.url_for(name)
        secs = flac_duration_seconds(data) if fmt == "flac" else None
        # Show the spoken reply (relevant); keep the url/size in the payload.
        self._trace.emit("tts", result.text, direction="out",
                         data={"format": fmt, "bytes": len(data), "seconds": round(secs, 1) if secs else None, "url": url})
        if self._s.voice_followup:
            # Announce path: end this run, then play the reply via the announce API
            # which AWAITS real playback end (no tail-clipping / duration guessing) and
            # start_conversation re-opens the mic — follow-up without a new wake word.
            self._run_end()
            assert self._cli is not None
            try:
                await self._cli.send_voice_assistant_announcement_await_response(
                    media_id=url, timeout=min((secs or 6) + 25, 120.0),
                    text=result.text, start_conversation=True)
                self._conversing = True  # mic re-opened → in a session
            except Exception as err:  # noqa: BLE001
                self._trace.emit("error", f"announce/follow-up failed: {str(err)[:120]}", level="error")
        else:
            self._event(_EVT.VOICE_ASSISTANT_TTS_END, {"url": url})
            # Hold the turn until playback finishes (device plays the URL async). It
            # only STARTS ~1s after we send the URL (fetch+decode+buffer), so a
            # generous tail buffer keeps RUN_END from clipping the end.
            if secs:
                await asyncio.sleep(min(secs + 1.6, 40.0))
        self.last_activity = "klaar"
        return True

    async def _play_wake_chime(self) -> None:
        """Play the bundled wake 'beep' (the upstream wake_word_triggered flac) on the
        local/Bluetooth sink at wake (wake_chime). paplay decodes flac directly."""
        try:
            from pathlib import Path
            from .local_play import play_file
            flac = Path(__file__).parent / "assets" / "wake_word_triggered.flac"
            await play_file(str(flac), self._s.audio_sink, self._trace)
        except Exception as err:  # noqa: BLE001 - a cue must never break the turn
            self._trace.emit("info", f"wake chime failed: {str(err)[:80]}")

    async def _play_chime(self) -> None:
        """Play the end-of-session chime so the user knows the mic closed."""
        if self._s.audio_sink:
            # Local-sink mode: the device may have no speaker — play the end chime on the
            # host/BT sink instead of the device announce path.
            try:
                import os
                import tempfile
                from .audio import make_chime_flac
                from .local_play import play_file
                if self._end_chime_path is None:
                    p = os.path.join(tempfile.gettempdir(), "respeaker_end_chime.flac")
                    with open(p, "wb") as f:
                        f.write(make_chime_flac())
                    self._end_chime_path = p
                await play_file(self._end_chime_path, self._s.audio_sink, self._trace)
            except Exception as err:  # noqa: BLE001
                self._trace.emit("info", f"end chime failed: {str(err)[:80]}")
            return
        if self._chime_url is None:
            try:
                from .audio import make_chime_flac
                self._audio_srv.publish("chime.flac", make_chime_flac(), "audio/flac")
                self._chime_url = self._audio_srv.url_for("chime.flac")
            except Exception as err:  # noqa: BLE001
                self._trace.emit("info", f"chime unavailable: {str(err)[:80]}")
                self._chime_url = ""
        if not self._chime_url or self._cli is None:
            return
        try:
            await self._cli.send_voice_assistant_announcement_await_response(
                media_id=self._chime_url, timeout=8.0, start_conversation=False)
        except Exception as err:  # noqa: BLE001
            self._trace.emit("info", f"chime failed: {str(err)[:80]}")

    def _run_end(self) -> None:
        """Send RUN_END at most once per turn (the announce/follow-up path ends the
        run early, and _finish's finally must not send a second one)."""
        if self._ended:
            return
        self._ended = True
        self._event(_EVT.VOICE_ASSISTANT_RUN_END)

    def _event(self, event_type: VoiceAssistantEventType, data: dict[str, str] | None = None) -> None:
        if self._cli is not None:
            try:
                self._cli.send_voice_assistant_event(event_type, data)
            except Exception:  # noqa: BLE001
                pass
