"""Streaming speech segmentation (end-of-utterance detection).

Feed 16 kHz float32 audio in any chunk size; get events:
  {"event": "speech_start", "t": seconds}
  {"event": "speech_end", "t": seconds, "audio": np.ndarray, "duration": s, "reason": "silence|max_length|flush"}
Silence between utterances is discarded (never sent to STT). A short pre-roll
is kept so the first syllable is not clipped, and utterances shorter than
MIN_SPEECH_MS are dropped as noise (coughs, taps).
"""
import numpy as np

from . import vad as vadmod

FRAME = vadmod.FRAME
FRAME_S = vadmod.FRAME_MS / 1000


class Segmenter:
    def __init__(self, vad, eou_ms=800, min_speech_ms=250, max_utterance_s=30, preroll_ms=300, start_frames=3):
        self.vad = vad
        self.eou_frames = max(1, eou_ms // vadmod.FRAME_MS)
        self.min_frames = max(1, min_speech_ms // vadmod.FRAME_MS)
        self.max_frames = int(max_utterance_s / FRAME_S)
        self.preroll = max(0, preroll_ms // vadmod.FRAME_MS)
        self.start_frames = start_frames  # consecutive speech frames needed to trigger
        self.reset()

    def reset(self):
        self.pending = np.zeros(0, dtype=np.float32)
        self.ring = []          # recent non-speech frames (pre-roll)
        self.utt = []           # frames of the current utterance
        self.in_speech = False
        self.speech_frames = 0  # voiced frames inside the utterance
        self.silence_run = 0
        self.voiced_run = 0
        self.t = 0.0            # stream time at the end of processed audio

    @property
    def active(self):
        return self.in_speech

    def current_audio(self):
        return np.concatenate(self.utt) if self.utt else np.zeros(0, dtype=np.float32)

    def feed(self, x):
        events = []
        self.pending = np.concatenate([self.pending, np.asarray(x, dtype=np.float32)])
        n = len(self.pending) // FRAME
        for i in range(n):
            f = self.pending[i * FRAME:(i + 1) * FRAME]
            self.t += FRAME_S
            speech = self.vad.is_speech(f)
            if not self.in_speech:
                self.ring.append(f)
                self.voiced_run = self.voiced_run + 1 if speech else 0
                if self.voiced_run >= self.start_frames:
                    self.in_speech = True
                    keep = self.preroll + self.start_frames
                    self.utt = self.ring[-keep:]
                    self.ring = []
                    self.speech_frames = self.voiced_run
                    self.silence_run = 0
                    events.append({"event": "speech_start", "t": round(self.t - FRAME_S * len(self.utt), 2)})
                elif len(self.ring) > self.preroll + self.start_frames:
                    self.ring.pop(0)
                continue
            self.utt.append(f)
            if speech:
                self.speech_frames += 1
                self.silence_run = 0
            else:
                self.silence_run += 1
            if self.silence_run >= self.eou_frames:
                events += self._end("silence")
            elif len(self.utt) >= self.max_frames:
                events += self._end("max_length")
        self.pending = self.pending[n * FRAME:]
        return events

    def _end(self, reason):
        audio = self.current_audio()
        if reason == "silence" and self.silence_run > 2:  # trim trailing silence, keep ~60 ms
            audio = audio[: max(0, len(audio) - (self.silence_run - 2) * FRAME)]
        voiced = self.speech_frames
        self.in_speech, self.utt, self.speech_frames, self.silence_run, self.voiced_run = False, [], 0, 0, 0
        if voiced < self.min_frames:
            return [{"event": "noise_discarded", "t": round(self.t, 2)}]
        return [{"event": "speech_end", "t": round(self.t, 2), "audio": audio,
                 "duration": round(len(audio) / vadmod.SR, 2), "reason": reason}]

    def flush(self):
        """Force-end the current utterance (client sent speech_ended / session_stop)."""
        if not self.in_speech:
            return []
        return self._end("flush")
