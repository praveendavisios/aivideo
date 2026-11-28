"""Audio format handling. Internal standard: 16 kHz, mono, float32 in [-1, 1].

Accepted inputs:
  * raw PCM: pcm_s16le | pcm_s32le | pcm_f32le, any sample rate, 1-8 channels (interleaved)
  * WAV files (PCM 16/24/32-bit or float), any rate/channels
  * other containers (webm/ogg/mp3/m4a) via ffmpeg, when ffmpeg is installed
Nothing is assumed about the ESP32 microphone format — the client declares it
in session_start and it is converted explicitly here.
"""
import io
import shutil
import subprocess
import wave

import numpy as np

TARGET_SR = 16000
FORMATS = {"pcm_s16le": (np.int16, 32768.0), "pcm_s32le": (np.int32, 2147483648.0), "pcm_f32le": (np.float32, 1.0)}


class AudioError(ValueError):
    pass


def pcm_to_float(data: bytes, audio_format="pcm_s16le", channels=1):
    if audio_format not in FORMATS:
        raise AudioError(f"Unsupported audio_format {audio_format}; use one of {', '.join(FORMATS)}")
    dtype, scale = FORMATS[audio_format]
    width = np.dtype(dtype).itemsize * channels
    if len(data) % width:
        data = data[: len(data) - len(data) % width]  # drop a trailing partial frame
    x = np.frombuffer(data, dtype=dtype).astype(np.float32) / scale
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)  # dual-mic → mono
    return x


def resample(x, sr, target=TARGET_SR):
    if sr == target or len(x) == 0:
        return x.astype(np.float32)
    from math import gcd
    try:
        from scipy.signal import resample_poly
        g = gcd(int(sr), int(target))
        return resample_poly(x, target // g, int(sr) // g).astype(np.float32)
    except ImportError:  # linear interpolation fallback
        n = int(round(len(x) * target / sr))
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def decode_wav(data: bytes):
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except wave.Error as exc:
        raise AudioError(f"Invalid WAV: {exc}") from exc
    if sw == 2:
        return pcm_to_float(raw, "pcm_s16le", ch), sr
    if sw == 4:
        return pcm_to_float(raw, "pcm_s32le", ch), sr
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        x = (ints.astype(np.float32) / 8388608.0).reshape(-1, ch).mean(axis=1)
        return x, sr
    if sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
        return x.reshape(-1, ch).mean(axis=1), sr
    raise AudioError(f"Unsupported WAV sample width {sw}")


def decode_any(data: bytes, timeout=20):
    """Decode an uploaded file of any common format to (float32 mono, sample_rate)."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            return decode_wav(data)
        except AudioError:
            pass  # e.g. WAVE_FORMAT_EXTENSIBLE float — let ffmpeg handle it
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("Only WAV is supported without ffmpeg installed")
    try:
        p = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(TARGET_SR),
                            "pipe:1"], input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("Audio decoding timed out") from exc
    if p.returncode != 0 or not p.stdout:
        raise AudioError(f"Could not decode audio: {p.stderr.decode(errors='ignore')[:200]}")
    return np.frombuffer(p.stdout, dtype=np.float32).copy(), TARGET_SR


def to_standard(x, sr):
    x = resample(np.asarray(x, dtype=np.float32), sr)
    return np.clip(x, -1.0, 1.0)


def float_to_pcm16(x):
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def to_wav_bytes(x, sr=TARGET_SR):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(float_to_pcm16(x))
    return buf.getvalue()


def duration(x, sr=TARGET_SR):
    return len(x) / float(sr)


def rms_db(x):
    if len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x))))
    return 20 * np.log10(max(r, 1e-6))
