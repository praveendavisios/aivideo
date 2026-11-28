"""Gentle noise handling, applied per utterance (not per chunk, to avoid edge artefacts).

Modes (MABCAT_NOISE_REDUCTION):
  off       — untouched
  highpass  — 80 Hz high-pass (removes hum, fan rumble, desk thumps) + peak normalisation. Default.
              Children's voices sit well above 80 Hz (fundamental ~250-400 Hz), so speech is unaffected.
  spectral  — highpass + stationary spectral gating (needs `pip install noisereduce`), reduced by at most 60 %.
Aggressive suppression smears consonants and hurts recognition of children's
speech, so nothing here removes more than that.
"""
import logging

import numpy as np

log = logging.getLogger("mabcat.audio")


def highpass(x, sr=16000, cutoff=80.0):
    try:
        from scipy.signal import butter, sosfiltfilt
        if len(x) < 64:
            return x
        sos = butter(2, cutoff, btype="highpass", fs=sr, output="sos")
        return sosfiltfilt(sos, x).astype(np.float32)
    except ImportError:
        return (x - np.mean(x)).astype(np.float32)  # at least remove DC


def normalise(x, peak_db=-3.0, max_gain_db=20.0):
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak < 1e-5:
        return x
    target = 10 ** (peak_db / 20)
    gain = min(target / peak, 10 ** (max_gain_db / 20))
    return (x * gain).astype(np.float32)


def spectral_gate(x, sr=16000):
    try:
        import noisereduce as nr
    except ImportError:
        log.warning("noisereduce not installed — spectral mode falls back to highpass")
        return x
    return nr.reduce_noise(y=x, sr=sr, stationary=True, prop_decrease=0.6).astype(np.float32)


def clean(x, sr=16000, mode="highpass"):
    if mode == "off" or len(x) == 0:
        return x
    y = highpass(x, sr)
    if mode == "spectral":
        y = spectral_gate(y, sr)
    return normalise(y)
