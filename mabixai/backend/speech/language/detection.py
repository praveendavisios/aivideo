"""Language detection for routing.

Decision order (first that is available wins, and the source is recorded):
  1. explicit session hint (console/robot setting "language": "ml")
  2. audio-level language ID from faster-whisper (if that provider is loaded)
  3. the student's preferred language from their profile
  4. "auto"
After transcription, code_switching.analyse() labels the transcript itself
(e.g. "ml-en") — that label, not the routing guess, is what the teacher sees.
"""
SUPPORTED = ["en", "ml", "hi", "ta", "kn", "te", "bn", "gu", "mr", "pa", "or", "ur"]


def normalise(code):
    if not code:
        return None
    code = code.lower().split("-")[0].split("_")[0]
    return {"eng": "en", "mal": "ml", "hin": "hi", "tam": "ta", "kan": "kn"}.get(code, code)


def route_language(session_hint=None, audio_lid=None, profile_language=None):
    for source, value in (("session", session_hint), ("audio-lid", audio_lid), ("profile", profile_language)):
        v = normalise(value)
        if v and v != "auto":
            return v, source
    return "auto", "none"


def audio_lid(samples, sample_rate=16000):
    """Language ID from audio via faster-whisper, if it is installed and loaded. Returns (code, prob) or (None, None)."""
    from ..providers import faster_whisper as fw
    if not fw.is_loaded():
        return None, None
    try:
        return fw.detect_language(samples, sample_rate)
    except Exception:
        return None, None
