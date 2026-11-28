"""Speech model router.

audio → preprocessing → language decision → route table → provider → transcript normalisation

Route table (MABCAT_STT_ROUTES, or config file MABCAT_STT_ROUTES_FILE as JSON), e.g.
  "en:faster-whisper,ml:indicconformer,hi:indicconformer,ml-en:indicconformer,*:faster-whisper"
The DEFAULT table below is a starting point only — replace it with the winners from
`python -m mabixai.backend.speech.benchmark` on your own recordings.

Every transcript records: provider, model, routing reason, and any provider that
was tried and failed first (fallback_from). Nothing switches silently.
"""
import json
import logging
import os
import threading

from ..config import settings
from .language import code_switching, detection
from .providers.base import ProviderUnavailable
from .providers.cloud_stt import OpenAISTTProvider
from .providers.faster_whisper import FasterWhisperProvider
from .providers.indicconformer import IndicConformerProvider

log = logging.getLogger("mabcat.stt.router")
DEFAULT_ROUTES = {"en": "faster-whisper", "ml": "indicconformer", "hi": "indicconformer", "ta": "indicconformer",
                  "kn": "indicconformer", "te": "indicconformer", "ml-en": "indicconformer", "hi-en": "indicconformer",
                  "*": "faster-whisper"}
REGISTRY = {"faster-whisper": FasterWhisperProvider, "indicconformer": IndicConformerProvider, "openai": OpenAISTTProvider}


def load_routes():
    routes = dict(DEFAULT_ROUTES)
    path = os.environ.get("MABCAT_STT_ROUTES_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            routes.update(json.load(fh))
    spec = os.environ.get("MABCAT_STT_ROUTES", "")
    for part in spec.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            routes[k.strip()] = v.strip()
    return routes


class SpeechRouter:
    def __init__(self, providers=None, routes=None, cloud_fallback=None):
        names = providers or settings.STT_PROVIDERS
        self.providers = {}
        for n in names:
            if n in REGISTRY:
                self.providers[n] = REGISTRY[n]()
        self.routes = routes or load_routes()
        self.cloud_fallback = settings.STT_CLOUD_FALLBACK if cloud_fallback is None else cloud_fallback
        self.lock = threading.Lock()

    def register(self, provider):
        """Add a provider instance (used by tests and custom engines)."""
        self.providers[provider.name] = provider

    def status(self):
        out = {}
        for n, p in self.providers.items():
            ok, detail = p.status()
            out[n] = {"available": ok, "detail": detail, "local": p.local, "streaming": p.streaming,
                      "languages": "multilingual (auto-detect)" if p.languages is None else sorted(p.languages),
                      "needs_language": p.needs_language}
        return {"providers": out, "routes": self.routes, "cloud_fallback": self.cloud_fallback}

    def candidates(self, language, mixed_with=None, prefer=None):
        """Ordered (provider, reason) list for a routed language."""
        key = code_switching.tag(language, mixed_with) if language not in (None, "auto") else "auto"
        order, seen = [], set()

        def add(name, reason):
            p = self.providers.get(name)
            if p and name not in seen and p.supports(language) and (p.local or name == prefer or self.cloud_fallback):
                order.append((p, reason))
                seen.add(name)
        if prefer:
            add(prefer, "requested")
        add(self.routes.get(key), f"route[{key}]")
        add(self.routes.get(language), f"route[{language}]")
        add(self.routes.get("*"), "route[*]")
        for n, p in self.providers.items():
            if p.local:
                add(n, "local fallback")
        if self.cloud_fallback:
            for n, p in self.providers.items():
                if not p.local:
                    add(n, "cloud fallback")
        return order

    def transcribe(self, audio, language=None, mixed_with=None, prefer=None, sample_rate=16000, on_partial=None):
        tried = []
        for provider, reason in self.candidates(language, mixed_with, prefer):
            ok, detail = provider.status()
            if not ok:
                tried.append({"provider": provider.name, "error": detail})
                continue
            try:
                kw = {"on_partial": on_partial} if on_partial and provider.streaming else {}
                t = provider.transcribe(audio, language=None if language == "auto" else language,
                                        sample_rate=sample_rate, **kw)
            except ProviderUnavailable as exc:
                tried.append({"provider": provider.name, "error": str(exc)})
                log.warning("STT %s unavailable: %s", provider.name, exc)
                continue
            except Exception as exc:
                tried.append({"provider": provider.name, "error": f"{exc.__class__.__name__}: {exc}"})
                log.exception("STT %s failed", provider.name)
                continue
            t.details.update(routing_reason=reason, routed_language=language, fallback_from=tried or None)
            t.details["transcript_language"] = code_switching.label(t.text) if t.text else None
            t.details["code_switching"] = code_switching.analyse(t.text) if t.text else None
            return t
        raise ProviderUnavailable("No speech-to-text provider could transcribe this audio: " +
                                  "; ".join(f"{x['provider']}: {x['error']}" for x in tried) if tried else
                                  "No STT provider configured for this language")


def normalise_text(text):
    """Whitespace/punctuation tidy only. Never translates, never changes words."""
    t = " ".join((text or "").split())
    return t[:1].upper() + t[1:] if t and t[0].isascii() else t


_router = None


def get_router():
    global _router
    if _router is None:
        _router = SpeechRouter()
    return _router


def set_router(r):
    global _router
    _router = r


def route_language(session_hint, profile_lang, audio=None):
    lid = None
    if session_hint in (None, "", "auto") and audio is not None:
        lid, _ = detection.audio_lid(audio)
    return detection.route_language(session_hint, lid, profile_lang)
