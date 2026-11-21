"""Environment-driven configuration for Mabix AI.

Every secret (API keys, admin password, Flask secret) is read from the
environment. Nothing sensitive is ever sent to the browser.
"""
import os
import secrets

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _load_dotenv():
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


class Config:
    ENV = os.environ.get("MABIX_ENV", "development").lower()
    IS_PRODUCTION = ENV == "production"

    SECRET_KEY = os.environ.get("MABIX_SECRET_KEY") or (
        None if IS_PRODUCTION else secrets.token_hex(32)
    )

    # --- Admin credentials -------------------------------------------------
    # Production: set MABIX_ADMIN_PASSWORD_HASH (werkzeug hash). The
    # development fallback admin/admin is refused when MABIX_ENV=production.
    ADMIN_USERNAME = os.environ.get("MABIX_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = os.environ.get("MABIX_ADMIN_PASSWORD_HASH")
    ADMIN_PASSWORD = os.environ.get("MABIX_ADMIN_PASSWORD")  # plain, dev only
    SESSION_HOURS = int(os.environ.get("MABIX_SESSION_HOURS", "12"))

    # --- Storage -----------------------------------------------------------
    DATA_DIR = os.path.abspath(os.environ.get("MABIX_DATA_DIR", os.path.join(BASE_DIR, "storage")))
    DB_PATH = os.environ.get("MABIX_DB_PATH", os.path.join(DATA_DIR, "mabix.db"))
    # Existing news pipeline writes to videos/<date>/ relative to the CWD.
    LEGACY_VIDEO_DIR = os.path.abspath(os.environ.get("MABIX_LEGACY_VIDEO_DIR", os.path.join(BASE_DIR, "videos")))
    MAX_UPLOAD_MB = int(os.environ.get("MABIX_MAX_UPLOAD_MB", "2048"))
    ALLOWED_VIDEO_EXT = {"mp4", "mov", "webm", "mkv", "m4v", "avi"}
    ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_AUDIO_EXT = {"mp3", "wav", "m4a", "aac", "ogg"}

    # --- AI models (defaults reproduce the existing app.py behaviour) --------
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.environ.get("MABIX_OLLAMA_MODEL", "llama3")
    SD_MODEL = os.environ.get("MABIX_SD_MODEL", "SG161222/Realistic_Vision_V5.1_noVAE")
    ANIMATEDIFF_ADAPTER = os.environ.get("MABIX_ANIMATEDIFF_ADAPTER", "guoyww/animatediff-motion-adapter-v1-5-2")

    # --- External APIs (never exposed to the frontend) ----------------------
    PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
    PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
    YOUTUBE_TOKEN_FILE = os.environ.get("MABIX_YOUTUBE_TOKEN", os.path.join(BASE_DIR, "youtube_token.json"))

    # --- Branding / rendering ----------------------------------------------
    LOGO_URL = os.environ.get("MABIX_LOGO_URL", "https://mabixai.in/assets/images/mabix-logo.png")
    FONT_PATH = os.environ.get("MABIX_FONT_PATH", "")

    DISABLE_WORKER = _bool("MABIX_DISABLE_WORKER", False)
