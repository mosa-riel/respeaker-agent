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


async def play_file(path: str, sink: str, trace: TraceBus) -> bool:
    """Play a WAV/audio file to PulseAudio `sink` (empty/"default" → host default sink)
    via `paplay`. Used to audition a saved recording on the Bluetooth/local speaker."""
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


async def play_pcm_stream(
    chunks: AsyncIterator[bytes],
    sink: str,
    rate: int,
    trace: TraceBus,
    *,
    channels: int = 1,
) -> bool:
    """Stream int16 mono PCM `chunks` to PulseAudio `sink`. Returns True on clean
    playback. `sink` == "default" plays to the host default sink."""
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
    try:
        async for c in chunks:
            if not c:
                continue
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
