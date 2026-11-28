"""AI4Bharat IndicConformer (600M multilingual) via Hugging Face transformers.

Model: ai4bharat/indic-conformer-600m-multilingual (MABCAT_INDIC_MODEL), loaded with
trust_remote_code=True. Decoders: "ctc" (faster) or "rnnt" (usually more accurate),
MABCAT_INDIC_DECODER.

Important properties (from the model card — verify against your benchmark):
  * Covers the 22 scheduled Indian languages; English is NOT one of them.
  * It needs the language code — it does not auto-detect — so the router only
    picks it when a language is known (session setting, audio LID or profile).
  * Output is in the native script; English words inside Malayalam speech
    come out transliterated in Malayalam script.
  * Licence: MIT on the model card at the time of writing; check before shipping.
No confidence score is exposed, so confidence is None.
"""
import importlib.util
import logging
import threading

from ...config import settings
from .base import ProviderUnavailable, STTProvider, Transcript

log = logging.getLogger("mabcat.stt.indic")
LANGS = {"as", "bn", "brx", "doi", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa",
         "sat", "sd", "ta", "te", "ur"}
_model = None
_device = "cpu"
_lock = threading.Lock()


def _get():
    global _model, _device
    with _lock:
        if _model is None:
            for mod in ("torch", "transformers"):
                if importlib.util.find_spec(mod) is None:
                    raise ProviderUnavailable(f"IndicConformer needs {mod} (pip install torch transformers torchaudio)")
            import torch
            from transformers import AutoModel
            try:
                _model = AutoModel.from_pretrained(settings.INDIC_MODEL, trust_remote_code=True)
            except Exception as exc:
                raise ProviderUnavailable(f"IndicConformer model could not be loaded ({settings.INDIC_MODEL}): {exc}") from exc
            if torch.cuda.is_available():
                try:
                    _model = _model.to("cuda")
                    _device = "cuda"
                except Exception as exc:  # remote-code models may not support .to()
                    log.warning("IndicConformer stays on CPU: %s", exc)
            log.info("IndicConformer loaded on %s", _device)
    return _model


class IndicConformerProvider(STTProvider):
    name = "indicconformer"
    languages = LANGS
    needs_language = True

    def status(self):
        missing = [m for m in ("torch", "transformers") if importlib.util.find_spec(m) is None]
        if missing:
            return False, f"needs {', '.join(missing)}"
        return True, f"{settings.INDIC_MODEL} [{settings.INDIC_DECODER}]" + (" (loaded)" if _model is not None else " (loads on first use)")

    def load(self):
        _get()

    def transcribe(self, audio, language=None, sample_rate=16000):
        if language not in LANGS:
            raise ProviderUnavailable(f"IndicConformer needs one of its languages, got {language!r}")
        model = _get()
        import torch
        wav = torch.from_numpy(audio).float().unsqueeze(0)
        if _device == "cuda":
            wav = wav.to("cuda")

        def run():
            with torch.inference_mode():
                return model(wav, language, settings.INDIC_DECODER)
        text, latency = self._timed(run)
        if isinstance(text, (list, tuple)):
            text = text[0] if text else ""
        return Transcript(text=str(text).strip(), language=language, provider=self.name, model=settings.INDIC_MODEL,
                          confidence=None, duration=round(len(audio) / sample_rate, 2), latency=round(latency, 3),
                          details={"decoder": settings.INDIC_DECODER, "device": _device})
