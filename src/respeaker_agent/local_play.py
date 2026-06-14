"""Local audio out — play TTS PCM to a host PulseAudio/PipeWire sink.

Used when `settings.audio_sink` is set (e.g. a paired Bluetooth A2DP speaker). We
stream the engine's int16 mono PCM (already at `tts_out_rate`) straight into
`paplay --raw` so playback starts as the first chunk arrives — same low-lag shape
as the device path. PulseAudio resamples/upmixes to the sink's native rate/channels.

No shell: `paplay` is spawned with an argument list. The only caller-supplied value
is the sink name, passed as a distinct argv element (never interpolated into a
command string).
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .trace import TraceBus


async def start_silence_keepalive(sink: str, trace: TraceBus):
    """Hold a (Bluetooth) sink open with an endless silence stream so it never suspends —
    instant playback, no first-second clip, no resume lag. PulseAudio mixes real audio
    over this. Returns the subprocess (terminate on shutdown) or None."""
    if not sink:
        return None
    args = ["paplay", "--raw", "--format=s16le", "--rate=48000", "--channels=1"]
    if sink != "default":
        args += ["--device", sink]
    args.append("/dev/zero")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        trace.emit("info", f"sink keepalive on {sink} (no suspend → instant playback)")
        return proc
    except FileNotFoundError:
        trace.emit("info", "paplay not found; no sink keepalive")
        return None


async def play_file(path: str, sink: str, trace: TraceBus, *, lead_ms: int = 0) -> bool:
    """Play a WAV/FLAC file to PulseAudio `sink` (empty/"default" → host default sink).
    With lead_ms > 0, prepend that much silence so a suspended Bluetooth sink wakes up
    during the silence instead of clipping the first second of audio."""
    if lead_ms <= 0:
        args = ["paplay"]
        if sink and sink != "default":
            args += ["--device", sink]
        args.append(path)
        try:
            proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            trace.emit("error", "paplay not found (install pulseaudio-utils)", level="error")
            return False
        rc = await proc.wait()
        if rc != 0:
            err = (await proc.stderr.read()).decode(errors="replace").strip()[:200] if proc.stderr else ""
            trace.emit("error", f"paplay rc={rc}: {err}", level="error")
            return False
        return True
    # Lead-silence path: decode → prepend zeros → play raw at the file's rate.
    try:
        import numpy as np
        import soundfile as sf
        data, rate = sf.read(path, dtype="int16", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data[:, 0]  # downmix to mono
        sil = np.zeros(int(rate * lead_ms / 1000), dtype="<i2")
        buf = np.concatenate([sil, data.astype("<i2")]).tobytes()
    except Exception as err:  # noqa: BLE001 - fall back to plain playback
        trace.emit("info", f"lead-silence decode failed ({str(err)[:60]}); plain play")
        return await play_file(path, sink, trace)

    async def _one(_: bytes):
        yield buf
    return await play_pcm_stream(_one(b""), sink, rate, trace)


async def play_pcm_stream(
    chunks: AsyncIterator[bytes],
    sink: str,
    rate: int,
    trace: TraceBus,
    *,
    channels: int = 1,
    lead_ms: int = 0,
) -> bool:
    """Stream int16 mono PCM `chunks` to PulseAudio `sink`. Returns True on clean
    playback. `sink` == "default" plays to the host default sink. lead_ms prepends that
    much silence so a suspended Bluetooth sink wakes during the silence, not by clipping
    the first audio."""
    args = ["paplay", "--raw", "--format=s16le", f"--rate={rate}", f"--channels={channels}"]
    if sink and sink != "default":
        args += ["--device", sink]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        trace.emit("error", "paplay not found (install pulseaudio-utils)", level="error")
        return False

    assert proc.stdin is not None
    silence = b"\x00\x00" * int(rate * channels * lead_ms / 1000) if lead_ms > 0 else b""
    try:
        first = True
        async for c in chunks:
            if not c:
                continue
            if first:
                # Write the lead silence RIGHT BEFORE the first real audio, not earlier:
                # a stream (TTS) has ~0.8s time-to-first-chunk, and silence written during
                # that gap drains and lets the BT sink re-suspend → clips anyway. Contiguous
                # silence→audio keeps the sink awake straight into the first sample.
                if silence:
                    proc.stdin.write(silence)
                first = False
            proc.stdin.write(c)
            await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # paplay died mid-stream; rc/stderr below tells why
    except Exception as err:  # noqa: BLE001 - synth errors shouldn't hang the turn
        proc.kill()
        trace.emit("error", f"local playback aborted: {str(err)[:120]}", level="error")
        await proc.wait()
        return False

    rc = await proc.wait()
    if rc != 0:
        err = (await proc.stderr.read()).decode(errors="replace").strip()[:200] if proc.stderr else ""
        trace.emit("error", f"paplay rc={rc}: {err}", level="error")
        return False
    return True
