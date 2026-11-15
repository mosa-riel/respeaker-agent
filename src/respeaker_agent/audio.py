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
    """A low, descending 'da-duuh' as FLAC bytes — played when a follow-up session
    closes (the device only decodes FLAC/MP3/Opus, not WAV). Descending + low = a
    clear 'we're done' feel."""
    import soundfile as sf

    def tone(freq: float, dur: float) -> np.ndarray:
        t = np.linspace(0, dur, int(rate * dur), endpoint=False)
        sig = np.sin(2 * np.pi * freq * t)
        # gentle attack + release per note so it doesn't click
        a = int(rate * 0.008)
        env = np.ones(sig.shape[0])
        env[:a] = np.linspace(0, 1, a)
        env[-a:] = np.linspace(1, 0, a)
        return sig * env

    # "da" (short, G3) → "duuh" (longer, lower C3) — descending, closing
    sig = np.concatenate([tone(196.0, 0.12), tone(130.81, 0.34)])
    fade = int(rate * 0.05)
    env = np.ones(sig.shape[0])
    env[-fade:] = np.linspace(1, 0, fade)
    sig = (sig * env * 0.85).astype(np.float32)  # near full scale: the device volume is already at max
    # Lead-in silence: an external speaker's amplifier wakes on the first audio and swallowed
    # most of this short chime (2026-11-15, chime often inaudible).
    sig = np.concatenate([np.zeros(int(rate * 0.3), dtype=np.float32), sig])
    buf = io.BytesIO()
    sf.write(buf, sig, rate, format="FLAC")
    return buf.getvalue()


def speaker_eq(x: np.ndarray, rate: int, highpass_hz: float, presence_db: float) -> np.ndarray:
    """Shape speech for a small speaker, zero-phase in the frequency domain: a 12 dB/octave
    high-pass at `highpass_hz` (a 4 cm driver reproduces almost nothing below ~250 Hz, but that
    energy still eats headroom and moves the cone) and a broad `presence_db` boost around 3 kHz,
    where intelligibility sits. 0 switches either off."""
    if (highpass_hz <= 0 and presence_db == 0) or x.size == 0:
        return x
    pad = int(rate * 0.1)  # room for the filter's ringing, so nothing wraps around
    spec = np.fft.rfft(np.concatenate([np.zeros(pad, x.dtype), x, np.zeros(pad, x.dtype)]))
    f = np.fft.rfftfreq(x.size + 2 * pad, 1 / rate)
    gain = np.ones_like(f)
    if highpass_hz > 0:
        with np.errstate(divide="ignore"):
            gain = gain / np.sqrt(1 + (highpass_hz / np.maximum(f, 1e-3)) ** 4)
    if presence_db:
        octaves = np.log2(np.maximum(f, 1.0) / 3000.0)
        gain = gain * 10 ** (presence_db * np.exp(-0.5 * (octaves / 0.6) ** 2) / 20)
    return np.fft.irfft(spec * gain, n=x.size + 2 * pad)[pad:pad + x.size].astype(np.float32)


def level(x: np.ndarray, rate: int, target_db: float = -18.0, window_s: float = 0.4, smooth_s: float = 0.25,
          max_up: float = 15.0, max_down: float = 6.0, gate_db: float = -50.0) -> np.ndarray:
    """Even out loudness over time. TTS voices fade on long replies (a 32 s poem went from about
    -13 to -30 dBFS), and one gain for the whole clip keeps the end quiet. Per ~0.4 s window the
    gain moves towards `target_db` (at most +max_up / -max_down dB); silence (below `gate_db`)
    gets the typical gain instead of being boosted; the gain is smoothed so it doesn't pump."""
    if x.size == 0:
        return x
    idx = np.arange(x.size)

    def mean_around(v: np.ndarray, width: int) -> np.ndarray:
        c = np.concatenate([[0.0], np.cumsum(v, dtype=np.float64)])
        lo, hi = np.clip(idx - width // 2, 0, x.size), np.clip(idx + width // 2, 0, x.size)
        return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)

    env_db = 10 * np.log10(mean_around(x.astype(np.float64) ** 2, max(1, int(rate * window_s))) + 1e-12)
    gain_db = np.clip(target_db - env_db, -max_down, max_up)
    voiced = env_db > gate_db
    if voiced.any():
        gain_db[~voiced] = np.median(gain_db[voiced])
    gain_db = mean_around(gain_db, max(1, int(rate * smooth_s)))
    return (x * 10 ** (gain_db / 20)).astype(np.float32)


def loud_flac(x: np.ndarray, rate: int, extra_db: float = 4.0, highpass_hz: float = 250.0,
              presence_db: float = 4.0, even: bool = True) -> bytes:
    """Mono float samples → a reply as loud and clear as the device allows, as FLAC (16-bit, with
    the sample count in STREAMINFO). The reSpeaker's only output control (its media player
    volume) is at max, while Voxtral replies peak around -9 dBFS. Shape it for a small speaker
    (speaker_eq), even out its loudness over time (level, when `even`), normalize the peak to
    -1 dBFS, then add `extra_db` with a soft limiter: samples above 0.6 bend smoothly towards 0.95
    instead of clipping, so speech gets louder without crackle."""
    import soundfile as sf

    x = speaker_eq(x.astype(np.float32), rate, highpass_hz, presence_db)
    if even:
        x = level(x, rate)
    # Normalize on the 99.7th percentile, not the single highest sample: a few transients (often
    # lifted by level) would otherwise hold the whole reply down; the limiter catches them.
    peak = float(np.percentile(np.abs(x), 99.7)) if x.size else 0.0
    if peak >= 1e-4:
        x = x * (0.89 / peak) * 10 ** (max(0.0, extra_db) / 20)
        t, c = 0.6, 0.95
        over = np.abs(x) > t
        x[over] = np.sign(x[over]) * (t + (c - t) * np.tanh((np.abs(x[over]) - t) / (c - t)))
    buf = io.BytesIO()
    sf.write(buf, x.astype(np.float32), rate, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def louder_flac(data: bytes, extra_db: float = 4.0, highpass_hz: float = 250.0, presence_db: float = 4.0,
                even: bool = True) -> bytes:
    """loud_flac for a FLAC file; on any problem the original bytes. libsndfile cannot read a
    streamed FLAC without a sample count (Voxtral's), so Voxtral replies go through pcm_to_loud_flac."""
    import soundfile as sf

    try:
        x, rate = sf.read(io.BytesIO(data), dtype="float32")
        return loud_flac(x.mean(axis=1) if x.ndim > 1 else x, rate, extra_db, highpass_hz, presence_db, even)
    except Exception:  # noqa: BLE001 - never lose the reply over loudness
        return data


def pcm_to_loud_flac(data: bytes, rate: int, extra_db: float = 4.0, highpass_hz: float = 250.0,
                     presence_db: float = 4.0, even: bool = True) -> bytes | None:
    """Raw float32 little-endian mono PCM (Voxtral 'pcm') → loud_flac; None when it does not look
    like float PCM, so the caller can fall back to the engine's own FLAC."""
    if not data or len(data) % 4:
        return None
    x = np.frombuffer(data, dtype="<f4")
    if not np.all(np.isfinite(x)) or float(np.max(np.abs(x))) > 4.0:
        return None
    return loud_flac(x, rate, extra_db, highpass_hz, presence_db, even)


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
