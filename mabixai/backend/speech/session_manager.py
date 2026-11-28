"""Audio session management for live speech.

One AudioSession per active microphone stream. It owns the format conversion,
VAD/segmentation, partial transcription scheduling, sequence checking and
turn-taking (barge-in). It is transport-agnostic: the WebSocket layer feeds it
bytes and sends whatever events it returns via the async `emit` callback.

Guarantees:
  * audio of different sessions/students never mixes (one segmenter per session)
  * each utterance produces at most one final_transcript (utterance ids)
  * duplicate / late chunks (sequence ≤ last seen) are dropped and counted
  * partials never block finals; a stale partial is discarded
"""
import asyncio
import logging
import time
import uuid

import numpy as np

from ..config import settings
from .audio import noise_reduction, preprocessing as pp, recorder, segmentation, vad as vadmod
from .providers.base import ProviderUnavailable
from .router import get_router, normalise_text, route_language

log = logging.getLogger("mabcat.stt.session")


class AudioSession:
    def __init__(self, emit, device_id, student, *, sample_rate=16000, channels=1, audio_format="pcm_s16le",
                 language="auto", provider=None, on_final=None, session_id=None):
        if audio_format not in pp.FORMATS:
            raise pp.AudioError(f"Unsupported audio_format {audio_format}")
        if not 8000 <= int(sample_rate) <= 48000 or not 1 <= int(channels) <= 8:
            raise pp.AudioError("sample_rate must be 8000-48000 and channels 1-8")
        self.id = session_id or f"aud_{uuid.uuid4().hex[:10]}"
        self.emit, self.device_id, self.student = emit, device_id, student
        self.sr, self.channels, self.format = int(sample_rate), int(channels), audio_format
        self.language, self.provider = language or "auto", provider
        self.on_final = on_final  # async callback(transcript_event) → AI teacher
        self.vad = vadmod.create(settings.VAD_ENGINE, settings.VAD_AGGRESSIVENESS)
        self.seg = segmentation.Segmenter(self.vad, settings.END_OF_UTTERANCE_MS, settings.MIN_SPEECH_MS,
                                          settings.MAX_UTTERANCE_S)
        self.last_seq = -1
        self.dropped = 0
        self.utt_n = 0
        self.utt_id = None
        self.final_ids = set()
        self.partial_task = None
        self.last_partial_at = 0.0
        self.robot_speaking = False
        self.bytes_in = 0
        self.started = time.time()
        self.closed = False

    # -------------------------------------------------------------- input
    def check_sequence(self, seq):
        if seq is None:
            return True
        seq = int(seq)
        if seq <= self.last_seq:
            self.dropped += 1
            return False
        if seq > self.last_seq + 1 and self.last_seq >= 0:
            log.info("%s: %d chunk(s) missing before seq %d", self.id, seq - self.last_seq - 1, seq)
        self.last_seq = seq
        return True

    async def feed(self, data: bytes, seq=None):
        if self.closed:
            return
        if not self.check_sequence(seq):
            return
        self.bytes_in += len(data)
        if self.bytes_in > settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024 * 50:  # hard stop on runaway streams
            await self.emit({"type": "transcription_error", "session_id": self.id, "code": "stream_too_large",
                             "message": "Audio stream exceeded the session limit"})
            return
        x = pp.to_standard(pp.pcm_to_float(data, self.format, self.channels), self.sr)
        for ev in self.seg.feed(x):
            await self._handle(ev)
        if self.seg.active and time.time() - self.last_partial_at > settings.STT_PARTIAL_INTERVAL_MS / 1000:
            self._schedule_partial()

    async def speech_ended(self):
        """Client-side end of utterance (e.g. push-to-talk released)."""
        for ev in self.seg.flush():
            await self._handle(ev)

    async def _handle(self, ev):
        if ev["event"] == "speech_start":
            self.utt_n += 1
            self.utt_id = f"{self.id}-u{self.utt_n}"
            await self.emit({"type": "speech_detected", "session_id": self.id, "utterance_id": self.utt_id, "t": ev["t"]})
            if self.robot_speaking:  # barge-in: the child talks over Mab Cat
                self.robot_speaking = False
                await self.emit({"type": "stop_speaking", "session_id": self.id, "reason": "student_interrupted"})
        elif ev["event"] == "speech_end":
            uid = self.utt_id
            await self.emit({"type": "processing_status", "session_id": self.id, "utterance_id": uid,
                             "status": "transcribing", "duration": ev["duration"]})
            if self.partial_task and not self.partial_task.done():
                self.partial_task.cancel()
            await self._final(ev["audio"], uid)
        elif ev["event"] == "noise_discarded":
            await self.emit({"type": "processing_status", "session_id": self.id, "status": "noise_ignored"})

    # -------------------------------------------------------- transcription
    def _route(self, audio):
        profile = getattr(self.student, "preferred_language", None) if self.student else None
        hint = self.language.split("-")[0] if self.language else None
        mixed = self.language.split("-")[1] if self.language and "-" in self.language else None
        lang, source = route_language(hint, profile, audio)
        return lang, mixed, source

    def _schedule_partial(self):
        if self.partial_task and not self.partial_task.done():
            return
        self.last_partial_at = time.time()
        audio, uid = self.seg.current_audio(), self.utt_id
        if len(audio) < 16000 * 0.6:
            return
        self.partial_task = asyncio.create_task(self._partial(audio, uid))

    async def _partial(self, audio, uid):
        lang, mixed, _ = self._route(None)
        try:
            t = await asyncio.to_thread(get_router().transcribe, audio, lang, mixed, self.provider)
        except Exception:
            return  # partials are best-effort
        if uid != self.utt_id or uid in self.final_ids or not t.text:
            return  # stale
        await self.emit({"type": "partial_transcript", "session_id": self.id, "utterance_id": uid,
                         "text": normalise_text(t.text), "language": t.details.get("transcript_language") or t.language,
                         "is_final": False, "provider": t.provider})

    async def _final(self, audio, uid):
        if uid in self.final_ids:
            return
        self.final_ids.add(uid)
        audio = noise_reduction.clean(audio, 16000, settings.NOISE_REDUCTION)
        lang, mixed, source = self._route(audio)
        if lang != "auto":
            await self.emit({"type": "language_detected", "session_id": self.id, "utterance_id": uid,
                             "language": lang, "source": source})
        t0 = time.perf_counter()
        try:
            t = await asyncio.wait_for(asyncio.to_thread(get_router().transcribe, audio, lang, mixed, self.provider),
                                       timeout=settings.STT_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.emit({"type": "transcription_error", "session_id": self.id, "utterance_id": uid,
                             "code": "timeout", "message": f"Transcription took longer than {settings.STT_TIMEOUT_S}s"})
            return
        except ProviderUnavailable as exc:
            await self.emit({"type": "transcription_error", "session_id": self.id, "utterance_id": uid,
                             "code": "no_provider", "message": str(exc)})
            return
        consent = bool(getattr(self.student, "consent_audio", False))
        recorder.save(audio, self.id, uid, consent)
        text = normalise_text(t.text)
        event = {"type": "final_transcript", "session_id": self.id, "utterance_id": uid, "text": text,
                 "language": t.details.get("transcript_language") or t.language, "provider_language": t.language,
                 "is_final": True, "provider": t.provider, "model": t.model, "confidence": t.confidence,
                 "duration": t.duration, "processing_ms": round((time.perf_counter() - t0) * 1000),
                 "rtf": t.rtf, "routing": {"reason": t.details.get("routing_reason"), "language_source": source,
                                           "fallback_from": t.details.get("fallback_from")},
                 "code_switching": t.details.get("code_switching")}
        if t.words:
            event["words"] = t.words
        await self.emit(event)
        if text and self.on_final:
            await self.on_final(event)

    async def stop(self):
        await self.speech_ended()
        self.closed = True
        if self.partial_task and not self.partial_task.done():
            self.partial_task.cancel()

    def stats(self):
        return {"session_id": self.id, "seconds": round(time.time() - self.started, 1), "bytes": self.bytes_in,
                "utterances": self.utt_n, "dropped_chunks": self.dropped, "vad": self.vad.name}


def transcribe_file(data: bytes, language="auto", provider=None, student=None):
    """One-shot transcription for the REST endpoint (uploaded recordings)."""
    if len(data) > settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024:
        raise pp.AudioError(f"Audio larger than {settings.MAX_AUDIO_UPLOAD_MB} MB")
    x, sr = pp.decode_any(data)
    x = pp.to_standard(x, sr)
    dur = pp.duration(x)
    if dur < 0.2:
        raise pp.AudioError("Audio too short")
    if dur > 600:
        raise pp.AudioError("Audio longer than 10 minutes — use the streaming endpoint")
    x = noise_reduction.clean(x, 16000, settings.NOISE_REDUCTION)
    hint = (language or "auto").split("-")[0]
    mixed = language.split("-")[1] if language and "-" in language else None
    lang, source = route_language(hint, getattr(student, "preferred_language", None), x)
    t = get_router().transcribe(x, lang, mixed, provider)
    out = t.to_dict()
    out["text"] = normalise_text(t.text)
    out["language_source"] = source
    out["speech_ratio"] = round(vadmod.speech_ratio(x, vadmod.create(settings.VAD_ENGINE)), 2)
    return out
