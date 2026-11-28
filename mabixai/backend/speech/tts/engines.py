"""Text-to-speech engines behind one interface.

Each engine yields audio sentence by sentence (chunked streaming): the first
sentence can play while later ones are still being synthesised.

  kokoro  — Kokoro-82M (Apache-2.0 weights). 24 kHz. Languages: en-US, en-GB, hi, es, fr, it, ja, pt-BR, zh.
            No Malayalam/Tamil/Kannada voice. pip install kokoro (Python 3.10–3.12) + espeak-ng.
  piper   — Piper ONNX voices, 16–22 kHz, very light (CPU real-time on a Raspberry Pi).
            piper-tts ≥ 1.3 is GPL-3.0 (piper1-gpl); 1.2.0 is MIT. Each voice has its own licence.
            Configure voice files per language: MABCAT_PIPER_MODELS="en=/voices/en_US-lessac-medium.onnx,hi=…"
  espeak  — espeak-ng (GPL-3.0 binary, used as a separate process). Robotic but covers ml/ta/kn/hi/en; dev fallback.
  gtts    — Google Translate TTS, needs internet; audio leaves the server. Off unless listed.
"""
import importlib.util
import io
import logging
import re
import shutil
import subprocess
import threading

import numpy as np

from ...config import settings
from ..audio import preprocessing as pp

log = logging.getLogger("mabcat.tts")
SENT_RE = re.compile(r"(?<=[.!?।॥])\s+")


def sentences(text, max_len=220):
    out = []
    for s in SENT_RE.split((text or "").strip()):
        s = s.strip()
        while len(s) > max_len:  # split very long sentences on commas/spaces
            cut = s.rfind(",", 0, max_len)
            cut = cut if cut > 40 else s.rfind(" ", 0, max_len)
            cut = cut if cut > 0 else max_len
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    return out


class TTSUnavailable(RuntimeError):
    pass


class TTSEngine:
    name = "base"
    languages = set()

    def status(self):
        raise NotImplementedError

    def supports(self, lang):
        return lang in self.languages

    def synth_sentence(self, text, lang, voice=None):
        """Return (float32 mono samples, sample_rate)."""
        raise NotImplementedError

    def stream(self, text, lang="en", voice=None):
        for s in sentences(text):
            yield self.synth_sentence(s, lang, voice)


class KokoroEngine(TTSEngine):
    name = "kokoro"
    LANG = {"en": "a", "en-gb": "b", "hi": "h", "es": "e", "fr": "f", "it": "i", "ja": "j", "pt": "p", "zh": "z"}
    DEFAULT_VOICE = {"a": "af_heart", "b": "bf_emma", "h": "hf_alpha", "e": "ef_dora", "f": "ff_siwis",
                     "i": "if_sara", "j": "jf_alpha", "p": "pf_dora", "z": "zf_xiaobei"}
    languages = set(LANG)

    def __init__(self):
        self.pipes = {}
        self.lock = threading.Lock()

    def status(self):
        if importlib.util.find_spec("kokoro") is None:
            return False, "kokoro not installed (needs Python 3.10–3.12)"
        return True, f"Kokoro-82M, voice {settings.KOKORO_VOICE}, 24 kHz"

    def _pipe(self, code):
        with self.lock:
            if code not in self.pipes:
                from kokoro import KPipeline
                self.pipes[code] = KPipeline(lang_code=code)
            return self.pipes[code]

    def synth_sentence(self, text, lang, voice=None):
        code = self.LANG.get(lang, "a")
        v = voice or (settings.KOKORO_VOICE if code == "a" else self.DEFAULT_VOICE[code])
        parts = [np.asarray(a, dtype=np.float32) for _, _, a in self._pipe(code)(text, voice=v) if a is not None]
        return (np.concatenate(parts) if parts else np.zeros(0, np.float32)), 24000


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self):
        self.paths = {}
        for part in settings.PIPER_MODELS.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                self.paths[k.strip()] = v.strip()
        if settings.PIPER_MODEL and "en" not in self.paths:
            self.paths["en"] = settings.PIPER_MODEL
        self.voices = {}
        self.languages = {k for k, v in self.paths.items()}

    def status(self):
        if importlib.util.find_spec("piper") is None:
            return False, "piper-tts not installed"
        import os
        ok = {k: v for k, v in self.paths.items() if os.path.exists(v)}
        if not ok:
            return False, "no Piper voice files configured (MABCAT_PIPER_MODELS)"
        self.languages = set(ok)
        return True, "voices: " + ", ".join(f"{k}={os.path.basename(v)}" for k, v in ok.items())

    def _voice(self, lang):
        if lang not in self.voices:
            from piper import PiperVoice
            self.voices[lang] = PiperVoice.load(self.paths[lang])
        return self.voices[lang]

    def synth_sentence(self, text, lang, voice=None):
        v = self._voice(lang)
        chunks = list(v.synthesize(text))
        if not chunks:
            return np.zeros(0, np.float32), 22050
        pcm = b"".join(c.audio_int16_bytes for c in chunks)
        return pp.pcm_to_float(pcm, "pcm_s16le", 1), chunks[0].sample_rate


class EspeakEngine(TTSEngine):
    name = "espeak"
    languages = {"en", "ml", "hi", "ta", "kn", "te", "bn", "gu", "mr", "pa", "ur", "es", "fr", "de"}
    VOICE = {"en": "en-us"}

    def status(self):
        exe = shutil.which("espeak-ng")
        return (True, "espeak-ng (robotic voice — development fallback)") if exe else (False, "espeak-ng not installed")

    def synth_sentence(self, text, lang, voice=None):
        exe = shutil.which("espeak-ng")
        if not exe:
            raise TTSUnavailable("espeak-ng not installed")
        p = subprocess.run([exe, "-v", voice or self.VOICE.get(lang, lang), "-s", "150", "--stdout", text],
                           capture_output=True, timeout=30)
        if p.returncode != 0:
            raise TTSUnavailable(p.stderr.decode(errors="ignore")[:200])
        return pp.decode_wav(p.stdout)


class GTTSEngine(TTSEngine):
    name = "gtts"
    languages = {"en", "ml", "hi", "ta", "kn", "te", "bn", "gu", "mr", "ur", "es", "fr", "de"}

    def status(self):
        if importlib.util.find_spec("gtts") is None:
            return False, "gtts not installed"
        return True, "Google TTS (internet; audio processed by Google)"

    def synth_sentence(self, text, lang, voice=None):
        from gtts import gTTS
        buf = io.BytesIO()
        gTTS(text, lang=lang).write_to_fp(buf)
        return pp.decode_any(buf.getvalue())


ENGINES = {"kokoro": KokoroEngine, "piper": PiperEngine, "espeak": EspeakEngine, "gtts": GTTSEngine}
_instances = {}


def engine(name):
    if name not in _instances:
        _instances[name] = ENGINES[name]()
    return _instances[name]


def status():
    out = {}
    for n in ENGINES:
        ok, detail = engine(n).status()
        out[n] = {"available": ok, "detail": detail, "enabled": n in settings.TTS_PROVIDERS,
                  "languages": sorted(engine(n).languages)}
    return out


def choose(lang, prefer=None):
    """First enabled+available engine supporting the language. Returns engine or raises."""
    base = (lang or "en").split("-")[0]
    names = ([prefer] if prefer else []) + [n for n in settings.TTS_PROVIDERS if n != prefer]
    for n in names:
        if n not in ENGINES:
            continue
        e = engine(n)
        ok, _ = e.status()
        if ok and e.supports(base):
            return e
    raise TTSUnavailable(f"No TTS engine available for language {base!r} (enabled: {settings.TTS_PROVIDERS})")


def synthesize_stream(text, lang="en", prefer=None, out_rate=16000):
    """Yield dicts {pcm: bytes (s16le @ out_rate), sample_rate, engine, text} per sentence."""
    from ..language import code_switching
    for s in sentences(text):
        sl = code_switching.analyse(s)["primary"]
        sl = lang if sl in ("und",) else sl
        e = choose(sl, prefer)
        x, sr = e.synth_sentence(s, sl if e.supports(sl) else (lang or "en"))
        if out_rate and sr != out_rate:
            x = pp.resample(x, sr, out_rate)
            sr = out_rate
        yield {"pcm": pp.float_to_pcm16(x), "sample_rate": sr, "engine": e.name, "text": s, "language": sl}
