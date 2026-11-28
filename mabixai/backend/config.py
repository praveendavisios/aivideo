"""MabixAI RoboCat configuration — environment variables only, never sent to clients."""
import os
import secrets

from mabix.config import BASE_DIR  # reuses the studio's .env loader


def _bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    ENV = os.environ.get("MABIX_ENV", "development").lower()
    IS_PRODUCTION = ENV == "production"
    SECRET_KEY = os.environ.get("MABCAT_SECRET_KEY") or os.environ.get("MABIX_SECRET_KEY") or (
        None if IS_PRODUCTION else secrets.token_hex(32))

    DATA_DIR = os.path.abspath(os.environ.get("MABCAT_DATA_DIR", os.path.join(BASE_DIR, "storage", "mabcat")))
    DATABASE_URL = os.environ.get("DATABASE_URL", "")  # e.g. postgresql+psycopg://user:pw@host/mabcat

    # Bootstrap admin (development default admin@mabix.local / admin — refused in production)
    ADMIN_EMAIL = os.environ.get("MABCAT_ADMIN_EMAIL") or "admin@mabix.local"
    ADMIN_PASSWORD = os.environ.get("MABCAT_ADMIN_PASSWORD") or ("" if IS_PRODUCTION else "admin")
    TOKEN_HOURS = _int("MABCAT_TOKEN_HOURS", 12)

    # Uploads
    MAX_DOC_MB = _int("MABCAT_MAX_DOC_MB", 50)
    DOC_EXT = {"pdf", "docx", "pptx", "txt", "md", "png", "jpg", "jpeg", "webp", "tif", "tiff"}
    OCR_LANGS = (os.environ.get("MABCAT_OCR_LANGS") or "eng")  # e.g. "eng+mal+hin" when tesseract packs installed

    # LLM reasoning: ollama | openai | gemini | none
    LLM_PROVIDER = (os.environ.get("MABCAT_LLM_PROVIDER") or "ollama")
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.environ.get("MABCAT_OLLAMA_MODEL", os.environ.get("MABIX_OLLAMA_MODEL", "llama3"))
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("MABCAT_OPENAI_MODEL", "gpt-4o-mini")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("MABCAT_GEMINI_MODEL", "gemini-1.5-flash")
    LLM_TIMEOUT = _int("MABCAT_LLM_TIMEOUT", 120)

    # Speech-to-text
    STT_PROVIDERS = [p.strip() for p in os.environ.get(
        "MABCAT_STT_PROVIDERS", "indicconformer,faster-whisper,openai").split(",") if p.strip()]
    STT_CLOUD_FALLBACK = _bool("MABCAT_STT_CLOUD_FALLBACK", False)
    WHISPER_MODEL = (os.environ.get("MABCAT_WHISPER_MODEL") or "large-v3")
    WHISPER_DEVICE = (os.environ.get("MABCAT_WHISPER_DEVICE") or "auto")      # auto | cuda | cpu
    WHISPER_COMPUTE = (os.environ.get("MABCAT_WHISPER_COMPUTE") or "auto")    # float16 | int8_float16 | int8
    INDIC_MODEL = os.environ.get("MABCAT_INDIC_MODEL", "ai4bharat/indic-conformer-600m-multilingual")
    INDIC_DECODER = (os.environ.get("MABCAT_INDIC_DECODER") or "rnnt")         # ctc | rnnt
    OPENAI_STT_MODEL = (os.environ.get("MABCAT_OPENAI_STT_MODEL") or "gpt-4o-mini-transcribe")
    OPENAI_STT_USD_PER_MIN = float(os.environ.get("MABCAT_OPENAI_STT_USD_PER_MIN", "0.003"))
    STT_PARTIAL_INTERVAL_MS = _int("MABCAT_STT_PARTIAL_INTERVAL_MS", 900)
    STT_TIMEOUT_S = _int("MABCAT_STT_TIMEOUT_S", 30)

    # Audio pipeline
    VAD_ENGINE = (os.environ.get("MABCAT_VAD") or "webrtc")          # webrtc | energy | silero
    VAD_AGGRESSIVENESS = _int("MABCAT_VAD_AGGRESSIVENESS", 2)    # 0-3 (webrtc)
    END_OF_UTTERANCE_MS = _int("MABCAT_EOU_MS", 800)             # silence that ends an utterance
    MIN_SPEECH_MS = _int("MABCAT_MIN_SPEECH_MS", 250)
    MAX_UTTERANCE_S = _int("MABCAT_MAX_UTTERANCE_S", 30)
    MAX_AUDIO_UPLOAD_MB = _int("MABCAT_MAX_AUDIO_MB", 20)
    NOISE_REDUCTION = (os.environ.get("MABCAT_NOISE_REDUCTION") or "highpass")  # off | highpass | spectral
    STORE_AUDIO = _bool("MABCAT_STORE_AUDIO", False)             # keep raw child audio (temporary only)
    AUDIO_RETENTION_HOURS = _int("MABCAT_AUDIO_RETENTION_HOURS", 24)

    # Text-to-speech: kokoro | piper | espeak | gtts (first available wins)
    TTS_PROVIDERS = [p.strip() for p in os.environ.get("MABCAT_TTS_PROVIDERS", "kokoro,piper,espeak").split(",") if p.strip()]
    KOKORO_VOICE = (os.environ.get("MABCAT_KOKORO_VOICE") or "af_heart")
    PIPER_MODEL = os.environ.get("MABCAT_PIPER_MODEL", "")       # path to <voice>.onnx
    PIPER_MODELS = os.environ.get("MABCAT_PIPER_MODELS", "")     # "ml=path.onnx,hi=path.onnx"

    # Conversation & data retention
    HEARTBEAT_S = _int("MABCAT_HEARTBEAT_S", 30)
    TRANSCRIPT_RETENTION_DAYS = _int("MABCAT_TRANSCRIPT_RETENTION_DAYS", 180)


settings = Settings()
os.makedirs(settings.DATA_DIR, exist_ok=True)
