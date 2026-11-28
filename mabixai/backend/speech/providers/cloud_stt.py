"""OpenAI cloud transcription (optional, opt-in).

Endpoint: POST https://api.openai.com/v1/audio/transcriptions
Models: gpt-4o-mini-transcribe / gpt-4o-transcribe (support `stream=true`, which sends
incremental `transcript.text.delta` events = partial transcripts) and whisper-1 (no streaming).
For continuous low-latency streaming OpenAI also offers Realtime transcription sessions
over WebSocket; this provider uses the per-utterance REST API (simpler, VAD is ours).

Cost: tracked per request as audio_minutes × MABCAT_OPENAI_STT_USD_PER_MIN (set this to
the current published price — prices change). Retries with backoff on 429/5xx; timeouts.
Child audio leaves your server when this provider is used — enable it only with consent.
"""
import json
import logging
import time

import requests

from ...config import settings
from ..audio.preprocessing import to_wav_bytes
from .base import ProviderUnavailable, STTProvider, Transcript

log = logging.getLogger("mabcat.stt.openai")
URL = "https://api.openai.com/v1/audio/transcriptions"
STREAMING_MODELS = ("gpt-4o-transcribe", "gpt-4o-mini-transcribe")


def record_cost(seconds, provider="openai", model=""):
    from ...db import session_scope
    from ...models import UsageCost
    usd = round(seconds / 60 * settings.OPENAI_STT_USD_PER_MIN, 6)
    try:
        with session_scope() as db:
            db.add(UsageCost(provider=f"{provider}:{model}", kind="stt", units=round(seconds, 2), usd=usd))
    except Exception as exc:
        log.warning("cost logging failed: %s", exc)
    return usd


class OpenAISTTProvider(STTProvider):
    name = "openai"
    languages = None
    local = False

    @property
    def streaming(self):
        return settings.OPENAI_STT_MODEL.startswith(STREAMING_MODELS)

    def status(self):
        if not settings.OPENAI_API_KEY:
            return False, "OPENAI_API_KEY not set"
        return True, settings.OPENAI_STT_MODEL

    def _post(self, audio, language, sample_rate, stream, on_partial=None):
        if not settings.OPENAI_API_KEY:
            raise ProviderUnavailable("OPENAI_API_KEY not set")
        data = {"model": settings.OPENAI_STT_MODEL, "response_format": "json"}
        if language and language != "auto":
            data["language"] = language
        if stream:
            data["stream"] = "true"
        files = {"file": ("audio.wav", to_wav_bytes(audio, sample_rate), "audio/wav")}
        last = None
        for attempt in range(3):
            try:
                r = requests.post(URL, headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}, data=data,
                                  files=files, timeout=settings.STT_TIMEOUT_S, stream=stream)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                    time.sleep(1.5 * 2 ** attempt)
                    continue
                if r.status_code != 200:
                    raise ProviderUnavailable(f"OpenAI STT HTTP {r.status_code}: {r.text[:200]}")
                if not stream:
                    return r.json().get("text", "")
                text = ""
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    ev = json.loads(payload)
                    if ev.get("type") == "transcript.text.delta":
                        text += ev.get("delta", "")
                        if on_partial:
                            on_partial(text)
                    elif ev.get("type") == "transcript.text.done":
                        text = ev.get("text", text)
                return text
            except requests.Timeout:
                last = "timeout"
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(1.5 * 2 ** attempt)
        raise ProviderUnavailable(f"OpenAI STT failed after retries: {last}")

    def transcribe(self, audio, language=None, sample_rate=16000, on_partial=None):
        t0 = time.perf_counter()
        text = self._post(audio, language, sample_rate, stream=self.streaming and on_partial is not None,
                          on_partial=on_partial)
        latency = time.perf_counter() - t0
        dur = len(audio) / sample_rate
        usd = record_cost(dur, "openai", settings.OPENAI_STT_MODEL)
        return Transcript(text=text.strip(), language=None if language in (None, "auto") else language,
                          provider=self.name, model=settings.OPENAI_STT_MODEL, confidence=None,
                          duration=round(dur, 2), latency=round(latency, 3), details={"usd": usd})
