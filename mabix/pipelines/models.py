"""GPU model manager.

Loads one heavy diffusion pipeline at a time so everything fits on a 16 GB
card (RTX 5080). Loading a different pipeline releases the previous one.

The SD and AnimateDiff loaders reproduce the original app.py init_pipe()
and init_animatediff() exactly; model IDs come from Settings/env.
"""
import gc
import logging
import threading

from .. import repo
from ..config import Config

log = logging.getLogger(__name__)

# Serialises every GPU call (same role as the original generation_lock).
generation_lock = threading.RLock()

_loaded = {"name": None, "model_id": None, "pipe": None}


def model_setting(key, default):
    try:
        return repo.get_setting(f"model.{key}", default) or default
    except Exception:
        return default


def _release():
    if _loaded["pipe"] is not None:
        log.info("Releasing GPU pipeline %s", _loaded["name"])
        _loaded.update(name=None, model_id=None, pipe=None)
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def _get(name, model_id, loader):
    with generation_lock:
        if _loaded["name"] == name and _loaded["model_id"] == model_id:
            return _loaded["pipe"]
        _release()
        log.info("Loading %s (%s)", name, model_id)
        pipe = loader(model_id)
        _loaded.update(name=name, model_id=model_id, pipe=pipe)
        return pipe


def _require_cuda():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU not available to PyTorch. On an RTX 50-series card install a CUDA 12.8+ "
                           "PyTorch build (pip install torch --index-url https://download.pytorch.org/whl/cu128).")
    return torch


# Quality presets: diffusion steps per engine.
QUALITY = {
    "fast": {"sd": 20, "animatediff": 20, "wan": 25},
    "balanced": {"sd": 28, "animatediff": 25, "wan": 35},
    "high": {"sd": 36, "animatediff": 30, "wan": 50},
}


def quality(kind):
    q = model_setting("quality", "balanced")
    return QUALITY.get(q, QUALITY["balanced"])[kind]


def get_sd(model_id=None):
    def load(mid):
        torch = _require_cuda()
        from diffusers import StableDiffusionPipeline
        torch.cuda.empty_cache()
        pipe = StableDiffusionPipeline.from_pretrained(mid, torch_dtype=torch.float16).to("cuda")
        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()
        pipe.vae.config.force_upcast = True
        return pipe
    return _get("sd", model_id or model_setting("sd", Config.SD_MODEL), load)


def get_animatediff(base_model=None):
    def load(ids):
        base, adapter_id = ids.split("|")
        torch = _require_cuda()
        from diffusers import AnimateDiffPipeline, DDIMScheduler, MotionAdapter
        adapter = MotionAdapter.from_pretrained(adapter_id, torch_dtype=torch.float16)
        pipe = AnimateDiffPipeline.from_pretrained(base, motion_adapter=adapter, torch_dtype=torch.float16)
        # Scheduler recommended for AnimateDiff (smoother, less flicker than the checkpoint default)
        pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config, clip_sample=False,
                                                   timestep_spacing="linspace", beta_schedule="linear",
                                                   steps_offset=1)
        pipe = pipe.to("cuda")
        pipe.enable_vae_slicing()
        return pipe
    base = base_model or model_setting("sd", Config.SD_MODEL)
    return _get("animatediff", f"{base}|{model_setting('animatediff', Config.ANIMATEDIFF_ADAPTER)}", load)


WAN_DEFAULT = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
WAN22_DEFAULT = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
VIDEO_ENGINES = {
    "animatediff": "AnimateDiff (SD 1.5 motion adapter) — fastest, ~2 s clips, lowest quality",
    "wan": "Wan 2.1 T2V 1.3B — 480p, 16 fps, good motion (~8 GB VRAM)",
    "wan22": "Wan 2.2 TI2V 5B — 720p, 24 fps, best quality (RTX 5080, uses CPU offload)",
}
# engine -> (setting key, default id, fps, max frames, (portrait w,h))
WAN_SPECS = {
    "wan": ("wan", WAN_DEFAULT, 16, 81, (480, 832)),
    "wan22": ("wan22", WAN22_DEFAULT, 24, 121, (704, 1280)),
}


def video_engine():
    eng = model_setting("video_engine", "animatediff")
    return eng if eng in VIDEO_ENGINES else "animatediff"


def get_wan(engine="wan"):
    key, default, *_ = WAN_SPECS[engine]

    def load(mid):
        torch = _require_cuda()
        from diffusers import AutoencoderKLWan, WanPipeline
        vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.bfloat16)
        if engine == "wan22":
            pipe.enable_model_cpu_offload()  # 5B transformer + text encoder exceed 16 GB together
        else:
            pipe = pipe.to("cuda")
        return pipe
    return _get(engine, model_setting(key, default), load)


def svd_model_id():
    return model_setting("svd", "")  # empty = image-to-video motion disabled


def get_svd():
    model_id = svd_model_id()
    if not model_id:
        raise RuntimeError("Stable Video Diffusion is not configured (Settings → AI models).")

    def load(mid):
        torch = _require_cuda()
        from diffusers import StableVideoDiffusionPipeline
        pipe = StableVideoDiffusionPipeline.from_pretrained(mid, torch_dtype=torch.float16, variant="fp16")
        pipe.enable_model_cpu_offload()  # keeps peak VRAM well under 16 GB
        return pipe
    return _get("svd", model_id, load)


# ------------------------------------------------------------ generators
def _seed_gen(seed):
    import torch
    return torch.Generator(device="cuda").manual_seed(int(seed))


def generate_image(prompt, out_path, width=512, height=768, negative="", seed=42, steps=None, guidance=6.5,
                   base_model=None):
    """Text-to-image with the given (or configured) SD 1.5 checkpoint. Raises on failure."""
    pipe = get_sd(base_model)
    import torch
    with generation_lock, torch.inference_mode():
        image = pipe(prompt=prompt, negative_prompt=negative, width=width, height=height,
                     num_inference_steps=steps or quality("sd"), guidance_scale=guidance,
                     generator=_seed_gen(seed)).images[0]
    image.save(out_path)
    torch.cuda.empty_cache()
    return out_path


def generate_motion_clip(prompt, out_path, negative="", seed=42, width=512, height=512, frames=16, steps=None,
                         base_model=None):
    """Text-to-video clip with AnimateDiff (16 frames is what the adapter was trained on)."""
    pipe = get_animatediff(base_model)
    import imageio
    import numpy as np
    import torch
    with generation_lock, torch.inference_mode():
        result = pipe(prompt=prompt, negative_prompt=negative, num_frames=frames, width=width, height=height,
                      num_inference_steps=steps or quality("animatediff"), guidance_scale=7.5,
                      generator=_seed_gen(seed)).frames[0]
    imageio.mimsave(out_path, [np.array(f) for f in result], fps=8, quality=9)
    torch.cuda.empty_cache()
    return out_path


def wan_frames(duration, fps, max_frames):
    """Frames covering `duration` seconds, shaped 4k+1 as Wan requires."""
    n = int(round((duration or 5) * fps))
    n = (max(n, 33) - 1 + 3) // 4 * 4 + 1  # round UP to 4k+1 so the clip covers the narration
    return min(n, max_frames)


def generate_wan_clip(prompt, out_path, negative="", seed=42, aspect="9:16", duration=None, engine="wan"):
    """Text-to-video with Wan 2.1 / 2.2. Raises on failure."""
    _, _, fps, max_frames, (pw, ph) = WAN_SPECS[engine]
    pipe = get_wan(engine)
    import torch
    from diffusers.utils import export_to_video
    if aspect == "9:16":
        w, h = pw, ph
    elif aspect == "1:1":
        w = h = pw
    else:
        w, h = ph, pw
    with generation_lock, torch.inference_mode():
        result = pipe(prompt=prompt, negative_prompt=negative, height=h, width=w,
                      num_frames=wan_frames(duration, fps, max_frames), guidance_scale=5.0,
                      num_inference_steps=quality("wan"), generator=_seed_gen(seed)).frames[0]
    export_to_video(result, out_path, fps=fps)
    torch.cuda.empty_cache()
    return out_path


def generate_video_clip(prompt, out_path, negative="", seed=42, aspect="9:16", duration=None, base_model=None):
    """Dispatch text-to-video to the engine chosen in Settings."""
    engine = video_engine()
    if engine in WAN_SPECS:
        return generate_wan_clip(prompt, out_path, negative, seed, aspect, duration, engine)
    w, h = {"9:16": (512, 768), "1:1": (512, 512)}.get(aspect, (768, 512))
    return generate_motion_clip(prompt, out_path, negative, seed, width=w, height=h, frames=16,
                                base_model=base_model)


def animate_image(image_path, out_path, seed=42, portrait=True):
    """Image-to-video with Stable Video Diffusion (opt-in). Raises on failure."""
    pipe = get_svd()
    import torch
    from diffusers.utils import export_to_video
    from PIL import Image, ImageOps
    size = (576, 1024) if portrait else (1024, 576)
    img = ImageOps.fit(Image.open(image_path).convert("RGB"), size)
    with generation_lock, torch.inference_mode():
        result = pipe(img, height=size[1], width=size[0], decode_chunk_size=4,
                      motion_bucket_id=127, noise_aug_strength=0.02, generator=_seed_gen(seed)).frames[0]
    export_to_video(result, out_path, fps=7)
    torch.cuda.empty_cache()
    return out_path
