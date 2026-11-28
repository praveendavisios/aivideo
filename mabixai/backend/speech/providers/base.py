"""Common STT provider interface."""
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Transcript:
    text: str
    language: str | None            # language the provider reports/used (ISO 639-1)
    provider: str
    model: str
    confidence: float | None = None  # only when the provider gives a calibrated value — otherwise None
    duration: float = 0.0            # seconds of audio
    latency: float = 0.0             # seconds of processing
    words: list = field(default_factory=list)  # [{"word", "start", "end"}] when supported
    details: dict = field(default_factory=dict)  # provider-specific raw values (e.g. language_probability)

    @property
    def rtf(self):
        return round(self.latency / self.duration, 3) if self.duration else None

    def to_dict(self):
        d = asdict(self)
        d["rtf"] = self.rtf
        return d


class ProviderUnavailable(RuntimeError):
    pass


class STTProvider:
    name = "base"
    #: ISO codes this provider can transcribe; None = multilingual with auto-detect
    languages: set | None = None
    needs_language = False   # True if it cannot auto-detect (IndicConformer)
    streaming = False        # native streaming partials
    word_timestamps = False
    local = True

    def status(self):
        """(available: bool, detail: str) — must be cheap and must not download models."""
        raise NotImplementedError

    def load(self):
        """Load / warm up the model (may download). Raises ProviderUnavailable."""

    def supports(self, language):
        if language in (None, "auto"):
            return not self.needs_language
        return self.languages is None or language in self.languages

    def transcribe(self, audio, language=None, sample_rate=16000) -> Transcript:
        raise NotImplementedError

    def _timed(self, fn):
        t0 = time.perf_counter()
        out = fn()
        return out, time.perf_counter() - t0
