"""Voice activity detection on 16 kHz mono float32 audio, in 30 ms frames.

Engines (MABCAT_VAD):
  webrtc  — WebRTC VAD (webrtcvad-wheels). Fast, CPU-only, good default for a server.
  silero  — Silero VAD (pip install silero-vad, uses torch). More robust in noise; ~1 ms/frame on CPU.
  energy  — adaptive energy threshold. No dependencies; weakest in noise.
Requested engines that are not installed fall back to energy, and the engine
actually used is reported (VAD.name).
"""
import logging

import numpy as np

log = logging.getLogger("mabcat.vad")
FRAME_MS = 30
SR = 16000
FRAME = SR * FRAME_MS // 1000  # 480 samples


class EnergyVAD:
    name = "energy"

    def __init__(self, threshold_db=10.0, window=100):
        self.threshold_db = threshold_db
        self.window = window  # ~3 s of history
        self.hist = []

    def is_speech(self, frame):
        db = 20 * np.log10(max(float(np.sqrt(np.mean(frame ** 2))), 1e-6))
        self.hist.append(db)
        if len(self.hist) > self.window:
            self.hist.pop(0)
        floor = float(np.percentile(self.hist, 10))  # noise floor = quiet tail of recent frames
        return len(self.hist) >= 5 and db > max(floor + self.threshold_db, -50.0)


class WebRtcVAD:
    name = "webrtc"

    def __init__(self, aggressiveness=2):
        import webrtcvad
        self.v = webrtcvad.Vad(int(aggressiveness))

    def is_speech(self, frame):
        pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
        return self.v.is_speech(pcm, SR)


class SileroVAD:
    name = "silero"

    def __init__(self, threshold=0.5):
        import torch
        from silero_vad import load_silero_vad
        self.torch, self.model, self.threshold = torch, load_silero_vad(), threshold
        self.buf = np.zeros(0, dtype=np.float32)
        self.last = False

    def is_speech(self, frame):
        # Silero wants 512-sample windows at 16 kHz; re-block the 480-sample frames.
        self.buf = np.concatenate([self.buf, frame])
        while len(self.buf) >= 512:
            win, self.buf = self.buf[:512], self.buf[512:]
            p = float(self.model(self.torch.from_numpy(win), SR).item())
            self.last = p >= self.threshold
        return self.last


def create(engine="webrtc", aggressiveness=2):
    try:
        if engine == "webrtc":
            return WebRtcVAD(aggressiveness)
        if engine == "silero":
            return SileroVAD()
    except Exception as exc:  # not installed / failed to load
        log.warning("VAD %s unavailable (%s) — using energy VAD", engine, exc)
    return EnergyVAD()


def frames(x):
    n = len(x) // FRAME
    for i in range(n):
        yield x[i * FRAME:(i + 1) * FRAME]


def speech_ratio(x, vad):
    fs = list(frames(x))
    return sum(vad.is_speech(f) for f in fs) / len(fs) if fs else 0.0
