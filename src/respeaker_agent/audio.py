"""Audio conversion helpers — speed-first, no ffmpeg.

The device talks 16-bit signed PCM, mono. TTS engines hand us float32 PCM (Voxtral
`pcm`) or a WAV blob; STT wants a WAV upload. These helpers convert between those
shapes using numpy + soxr (a fast resampler). `audioop` is gone in Python 3.13+,
so everything here is numpy-based.
"""

from __future__ import annotations

import io
import wave

import numpy as np
import soxr

# soxr quality preset. "LQ" (low) is the speed/lag pick for a voice agent — the
# device speaker won't reveal HQ-vs-LQ on resampled speech.
RESAMPLE_QUALITY = "LQ"


def float32le_to_int16(data: bytes) -> np.ndarray:
    """Raw float32 LE PCM (e.g. Voxtral `pcm`) → int16 sample array."""
    f = np.frombuffer(data, dtype="<f4")
    return _f32_to_i16(f)


def _f32_to_i16(f: np.ndarray) -> np.ndarray:
    return np.clip(f * 32767.0, -32768, 32767).astype(np.int16)


def resample_int16(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample a mono int16 array. No-op when rates match."""
    if src_rate == dst_rate or samples.size == 0:
        return samples
    # Resample in int16 scale, then clip back. soxr keeps the input amplitude scale.
    out = soxr.resample(samples.astype(np.float32), src_rate, dst_rate, quality=RESAMPLE_QUALITY)
    return np.clip(out, -32768, 32767).astype(np.int16)


def wav_to_int16_mono(data: bytes) -> tuple[np.ndarray, int]:
    """Parse a WAV blob → (mono int16 array, sample_rate). Downmixes if stereo."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width: {width * 8}-bit (want 16-bit)")
    arr = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return arr, rate


def make_chime_flac(rate: int = 24000) -> bytes:
    """A short two-note 'bling' as FLAC bytes — played when a follow-up session ends
    (the device only decodes FLAC/MP3/Opus, not WAV)."""
    import soundfile as sf

    def tone(freq: float, dur: float) -> np.ndarray:
        t = np.linspace(0, dur, int(rate * dur), endpoint=False)
        return np.sin(2 * np.pi * freq * t)

    sig = np.concatenate([tone(784, 0.12), tone(1175, 0.16)])  # G5 → D6
    fade = int(rate * 0.03)
    env = np.ones(sig.shape[0])
    env[-fade:] = np.linspace(1, 0, fade)
    sig = (sig * env * 0.35).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, sig, rate, format="FLAC")
    return buf.getvalue()


def flac_duration_seconds(data: bytes) -> float | None:
    """Read clip length from a FLAC STREAMINFO header — no decode, so it's cheap and
    can't delay the response. Returns None if it can't parse."""
    if data[:4] != b"fLaC" or len(data) < 8 + 18:
        return None
    # 4 magic + 4 block header, then STREAMINFO; sample_rate(20)+ch(3)+bps(5)+
    # total_samples(36) live in bytes 10..18 of the STREAMINFO data.
    val = int.from_bytes(data[8 + 10:8 + 18], "big")
    sample_rate = val >> 44
    total_samples = val & ((1 << 36) - 1)
    if not sample_rate or not total_samples:
        return None
    return total_samples / sample_rate


def int16_to_wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    """Wrap a mono int16 array in a WAV container (for the STT upload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()
