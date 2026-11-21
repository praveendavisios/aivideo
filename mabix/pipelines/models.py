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


def get_sd():
    def load(model_id):
        torch = _require_cuda()
        from diffusers import StableDiffusionPipeline
        torch.cuda.empty_cache()
        pipe = StableDiffusionPipeline.from_pretrained(model_id, torch_dtype=torch.float16).to("cuda")
        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()
        pipe.vae.config.force_upcast = True
        return pipe
    return _get("sd", model_setting("sd", Config.SD_MODEL), load)


def get_animatediff():
    def load(ids):
        base, adapter_id = ids.split("|")
        torch = _require_cuda()
        from diffusers import AnimateDiffPipeline, MotionAdapter
        adapter = MotionAdapter.from_pretrained(adapter_id, torch_dtype=torch.float16)
        pipe = AnimateDiffPipeline.from_pretrained(base, motion_adapter=adapter, torch_dtype=torch.float16).to("cuda")
        pipe.enable_vae_slicing()
        return pipe
    ids = f"{model_setting('sd', Config.SD_MODEL)}|{model_setting('animatediff', Config.ANIMATEDIFF_ADAPTER)}"
    return _get("animatediff", ids, load)


WAN_DEFAULT = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
VIDEO_ENGINES = {
    "animatediff": "AnimateDiff (SD 1.5 motion adapter) — existing model, ~2 s clips",
    "wan": "Wan 2.1 T2V 1.3B — recommended for RTX 5080, ~5 s clips at 480p",
}


def video_engine():
    eng = model_setting("video_engine", "animatediff")
    return eng if eng in VIDEO_ENGINES else "animatediff"


def get_wan():
    def load(mid):
        torch = _require_cuda()
        from diffusers import AutoencoderKLWan, WanPipeline
        vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.bfloat16).to("cuda")
        return pipe
    return _get("wan", model_setting("wan", WAN_DEFAULT), load)


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


def generate_image(prompt, out_path, width=512, height=768, negative="", seed=42, steps=22, guidance=6.0):
    """Text-to-image with the configured SD checkpoint. Raises on failure."""
    pipe = get_sd()
    import torch
    with generation_lock, torch.inference_mode():
        image = pipe(prompt=prompt, negative_prompt=negative, width=width, height=height,
                     num_inference_steps=steps, guidance_scale=guidance,
                     generator=_seed_gen(seed)).images[0]
    image.save(out_path)
    torch.cuda.empty_cache()
    return out_path


def generate_motion_clip(prompt, out_path, negative="", seed=42, width=512, height=512, frames=16, steps=20):
    """Text-to-video clip with AnimateDiff. Raises on failure."""
    pipe = get_animatediff()
    import imageio
    import numpy as np
    import torch
    with generation_lock, torch.inference_mode():
        result = pipe(prompt=prompt, negative_prompt=negative, num_frames=frames, width=width, height=height,
                      num_inference_steps=steps, guidance_scale=7.5, generator=_seed_gen(seed)).frames[0]
    imageio.mimsave(out_path, [np.array(f) for f in result], fps=8)
    torch.cuda.empty_cache()
    return out_path


def generate_wan_clip(prompt, out_path, negative="", seed=42, portrait=True, frames=81):
    """Text-to-video with Wan 2.1 (480p, 16 fps). Raises on failure."""
    pipe = get_wan()
    import torch
    from diffusers.utils import export_to_video
    w, h = (480, 832) if portrait else (832, 480)
    with generation_lock, torch.inference_mode():
        result = pipe(prompt=prompt, negative_prompt=negative, height=h, width=w, num_frames=frames,
                      guidance_scale=5.0, generator=_seed_gen(seed)).frames[0]
    export_to_video(result, out_path, fps=16)
    torch.cuda.empty_cache()
    return out_path


def generate_video_clip(prompt, out_path, negative="", seed=42, aspect="9:16"):
    """Dispatch text-to-video to the engine chosen in Settings."""
    if video_engine() == "wan":
        return generate_wan_clip(prompt, out_path, negative, seed, portrait=(aspect == "9:16"))
    w, h = {"9:16": (512, 768), "1:1": (512, 512)}.get(aspect, (768, 512))
    return generate_motion_clip(prompt, out_path, negative, seed, width=w, height=h, frames=24)


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
