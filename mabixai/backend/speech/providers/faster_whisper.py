"""faster-whisper (CTranslate2 Whisper) — local GPU with CPU fallback.

Configuration: MABCAT_WHISPER_MODEL (tiny…large-v3, large-v3-turbo, or a local path),
MABCAT_WHISPER_DEVICE (auto|cuda|cpu), MABCAT_WHISPER_COMPUTE (auto|float16|int8_float16|int8).
Confidence: Whisper's avg_logprob is not a calibrated probability, so
`confidence` is reported as None; raw values are in `details`.
"""
import importlib.util
import logging
import threading

from ...config import settings
from .base import ProviderUnavailable, STTProvider, Transcript

log = logging.getLogger("mabcat.stt.whisper")
_model = None
_model_info = {}
_lock = threading.Lock()


def is_loaded():
    return _model is not None


def _device():
    dev, compute = settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE
    if dev == "auto":
        try:
            import ctranslate2
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            dev = "cpu"
    if compute == "auto":
        compute = "float16" if dev == "cuda" else "int8"
    return dev, compute


def get_model():
    global _model
    with _lock:
        if _model is None:
            if importlib.util.find_spec("faster_whisper") is None:
                raise ProviderUnavailable("faster-whisper is not installed (pip install faster-whisper)")
            from faster_whisper import WhisperModel
            dev, compute = _device()
            try:
                _model = WhisperModel(settings.WHISPER_MODEL, device=dev, compute_type=compute)
            except Exception as exc:
                if dev == "cuda":  # e.g. CUDA libs missing / unsupported GPU → CPU fallback
                    log.warning("Whisper on CUDA failed (%s) — falling back to CPU int8", exc)
                    dev, compute = "cpu", "int8"
                    try:
                        _model = WhisperModel(settings.WHISPER_MODEL, device=dev, compute_type=compute)
                    except Exception as exc2:
                        raise ProviderUnavailable(f"Whisper model could not be loaded: {exc2}") from exc2
                else:
                    raise ProviderUnavailable(f"Whisper model could not be loaded: {exc}") from exc
            _model_info.update(device=dev, compute=compute, model=settings.WHISPER_MODEL)
            log.info("faster-whisper loaded: %s", _model_info)
    return _model


def detect_language(audio, sample_rate=16000):
    m = get_model()
    if hasattr(m, "detect_language"):
        lang, prob, _ = m.detect_language(audio)
        return lang, prob
    _, info = m.transcribe(audio, beam_size=1, without_timestamps=True)
    return info.language, info.language_probability


class FasterWhisperProvider(STTProvider):
    name = "faster-whisper"
    languages = None  # 99 languages incl. en, ml, hi, ta, kn, te, bn, gu, mr, pa, ur
    word_timestamps = True

    def status(self):
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper not installed"
        if is_loaded():
            return True, f"{_model_info.get('model')} on {_model_info.get('device')} ({_model_info.get('compute')})"
        return True, f"{settings.WHISPER_MODEL} (loads on first use)"

    def load(self):
        get_model()

    def transcribe(self, audio, language=None, sample_rate=16000):
        model = get_model()
        lang = None if language in (None, "auto") else language

        def run():
            segments, info = model.transcribe(audio, language=lang, task="transcribe", beam_size=5,
                                              word_timestamps=True, vad_filter=False,
                                              condition_on_previous_text=False)
            segs = list(segments)
            return segs, info
        (segs, info), latency = self._timed(run)
        words = [{"word": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)}
                 for s in segs for w in (s.words or [])]
        return Transcript(text=" ".join(s.text.strip() for s in segs).strip(), language=info.language,
                          provider=self.name, model=_model_info.get("model", settings.WHISPER_MODEL),
                          confidence=None, duration=round(len(audio) / sample_rate, 2), latency=round(latency, 3),
                          words=words, details={"language_probability": round(info.language_probability, 3),
                                                "avg_logprob": [round(s.avg_logprob, 3) for s in segs],
                                                "device": _model_info.get("device")})
