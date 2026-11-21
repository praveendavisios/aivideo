"""Detects which AI dependencies are actually available, so the UI never
claims a feature works when its backend is missing."""
import importlib.util
import shutil
import threading
import time

from .config import Config

_cache = {"at": 0, "data": None}
_lock = threading.Lock()
TTL = 60


def _installed(mod):
    return importlib.util.find_spec(mod) is not None


def _gpu():
    if not _installed("torch"):
        return {"ok": False, "detail": "PyTorch not installed"}
    try:
        import torch
        if not torch.cuda.is_available():
            return {"ok": False, "detail": f"torch {torch.__version__}: CUDA not available"}
        name = torch.cuda.get_device_name(0)
        major, minor = torch.cuda.get_device_capability(0)
        arch = f"sm_{major}{minor}"
        archs = torch.cuda.get_arch_list()
        mem = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        cc = major * 10 + minor
        ptx_ok = any(a.startswith("compute_") and int(a[8:]) <= cc for a in archs if a[8:].isdigit())
        if archs and arch not in archs and not ptx_ok:
            return {"ok": False, "detail": f"{name}: this torch build lacks {arch} kernels — install a CUDA 12.8+ build (cu128)"}
        return {"ok": True, "detail": f"{name} · {mem:.0f} GB · torch {torch.__version__}"}
    except Exception as exc:
        return {"ok": False, "detail": f"GPU check failed: {exc}"}


def _diffusers(feature):
    if not _installed("diffusers"):
        return False, "diffusers not installed"
    try:
        import diffusers
        if feature and not hasattr(diffusers, feature):
            return False, f"diffusers {diffusers.__version__} lacks {feature} — upgrade diffusers"
        return True, f"diffusers {diffusers.__version__}"
    except Exception as exc:
        return False, str(exc)


def detect(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < TTL:
            return _cache["data"]
        from .pipelines import llm, models
        from .publishing import youtube

        llm_ok, llm_detail = llm.is_available()
        gpu = _gpu()
        engine = models.video_engine()
        feat = "WanPipeline" if engine in models.WAN_SPECS else "AnimateDiffPipeline"
        t2v_ok, t2v_detail = _diffusers(feat)
        svd_id = models.svd_model_id()
        svd_ok, svd_detail = _diffusers("StableVideoDiffusionPipeline") if svd_id else (False, "not configured")
        ffmpeg = shutil.which("ffmpeg") or (_installed("imageio_ffmpeg") and "imageio-ffmpeg")
        data = {
            "llm": {"ok": llm_ok, "label": "Script writer (Ollama)", "detail": llm_detail},
            "gpu": {"ok": gpu["ok"], "label": "GPU (CUDA)", "detail": gpu["detail"]},
            "image": {"ok": gpu["ok"] and _diffusers("StableDiffusionPipeline")[0], "label": "AI images (Stable Diffusion)",
                      "detail": models.model_setting("sd", Config.SD_MODEL)},
            "video": {"ok": gpu["ok"] and t2v_ok, "label": "AI video (text-to-video)",
                      "detail": f"{models.VIDEO_ENGINES[engine].split(' —')[0]} · {t2v_detail}"},
            "i2v": {"ok": gpu["ok"] and svd_ok, "label": "Image-to-video (SVD)",
                    "detail": svd_id or "not configured — set a model in Settings"},
            "tts": {"ok": _installed("gtts"), "label": "Voice narration (gTTS)",
                    "detail": "needs internet access" if _installed("gtts") else "gtts not installed"},
            "render": {"ok": _installed("moviepy") and bool(ffmpeg), "label": "Video renderer (moviepy/ffmpeg)",
                       "detail": "ready" if ffmpeg else "ffmpeg not found"},
            "stock": {"ok": bool(Config.PEXELS_KEY or Config.PIXABAY_KEY), "label": "Stock images (Pexels/Pixabay)",
                      "detail": ", ".join(n for n, k in (("Pexels", Config.PEXELS_KEY), ("Pixabay", Config.PIXABAY_KEY)) if k)
                      or "no API keys set — news falls back to AI images"},
            "youtube": {"ok": youtube.is_configured(), "label": "YouTube publishing",
                        "detail": "token found" if youtube.is_configured() else "youtube_token.json not found"},
            "lipsync": {"ok": False, "label": "Lip-sync",
                        "detail": "not available — needs a lip-sync model (e.g. Wav2Lip/SadTalker), not configured"},
        }
        _cache.update(at=time.time(), data=data)
        return data


def ready_for(module):
    """Return list of human-readable blockers for a module."""
    c = detect()
    need = ["llm", "tts", "render"]
    if module != "upload":
        need += ["gpu", "image"]
    return [f"{c[k]['label']}: {c[k]['detail']}" for k in need if not c[k]["ok"]]
