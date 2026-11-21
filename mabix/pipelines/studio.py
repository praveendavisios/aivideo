"""AI Video Studio: module definitions, script planning and scene visuals.

Every module follows the same pipeline:
  plan (LLM script + scene breakdown) → review/edit → storyboard keyframes
  (optional) → per-scene AI video → narration/captions/music → render.
"""
import logging
import os
import random

from . import llm, models
from .render import VOICES

log = logging.getLogger(__name__)

NEGATIVE = ("text, watermark, logo, signature, blurry, distorted, low quality, ugly, bad anatomy, "
            "deformed face, extra limbs, jpeg artifacts")

MODULES = {
    "short": {
        "label": "AI Short Videos", "icon": "zap", "input_label": "What is your video about?",
        "placeholder": "e.g. 5 surprising facts about the deep ocean",
        "styles": {
            "cinematic": "cinematic film still, dramatic lighting, shallow depth of field, 35mm",
            "realistic": "photorealistic, natural lighting, sharp focus, DSLR",
            "documentary": "documentary footage, handheld camera, natural colors",
            "anime": "anime style, vibrant colors, detailed cel shading",
            "3d": "3D render, pixar style, soft global illumination",
            "neon": "neon cyberpunk aesthetic, glowing lights, night city",
        },
        "durations": [15, 30, 45, 60], "image_input": True,
        "intro": "a fast-paced, engaging vertical short video for YouTube Shorts / Instagram Reels",
    },
    "animation": {
        "label": "AI Animation", "icon": "film", "input_label": "Describe your animation",
        "placeholder": "e.g. A curious robot explores a candy forest and makes a friend",
        "styles": {
            "2d": "2D flat animation style, clean vector shapes, bold outlines",
            "3d": "3D animated movie style, pixar, soft lighting, expressive characters",
            "cartoon": "cartoon style, exaggerated expressions, bright saturated colors",
            "educational": "educational explainer animation, clean infographic style, simple shapes",
            "cinematic": "cinematic animated film, dramatic lighting, detailed environment",
            "storybook": "storybook illustration, watercolor textures, warm soft colors",
        },
        "durations": [15, 30, 60, 90], "image_input": True,
        "intro": "an animated video with recurring characters, expressive movement and animated backgrounds",
    },
    "story": {
        "label": "AI Stories", "icon": "book", "input_label": "Story idea",
        "placeholder": "e.g. A lighthouse keeper discovers the light guides lost stars home",
        "styles": {
            "cinematic": "cinematic film still, dramatic lighting, rich colors",
            "storybook": "storybook illustration, watercolor textures, warm soft colors",
            "fantasy": "epic fantasy art, magical atmosphere, detailed environment",
            "realistic": "photorealistic, natural lighting, sharp focus",
            "3d": "3D animated movie style, soft global illumination",
        },
        "categories": {
            "educational": "Educational", "moral": "Moral", "children": "Children's",
            "inspirational": "Inspirational", "scifi": "Science fiction", "historical": "Historical",
            "fantasy": "Fantasy", "entertainment": "General entertainment",
        },
        "durations": [30, 60, 90, 120], "image_input": False,
        "intro": "a narrated story video with an introduction, main story and conclusion",
    },
    "answer": {
        "label": "Short Answer Videos", "icon": "help", "input_label": "Question or topic",
        "placeholder": "e.g. What is Artificial Intelligence?",
        "styles": {
            "educational": "clean educational illustration, simple shapes, infographic style, bright",
            "whiteboard": "whiteboard sketch illustration, hand drawn diagram, minimal",
            "3d": "3D explainer render, soft studio lighting, clean background",
            "realistic": "photorealistic, natural lighting, sharp focus",
        },
        "durations": [15, 30, 60, 90], "image_input": False,
        "intro": "a concise, accurate educational short that answers the question",
    },
}

ASPECTS = ["9:16", "16:9", "1:1"]
RESOLUTIONS = ["720p", "1080p"]
TRANSITIONS = {"crossfade": "Crossfade", "fade": "Fade through black", "none": "Hard cut"}
MOTION_MODES = {
    "t2v": "AI video per scene (text-to-video)",
    "i2v": "AI video from keyframes (Stable Video Diffusion)",
    "none": "AI keyframe images with camera motion",
}

DEFAULT_CONFIG = {
    "duration": 30, "aspect": "9:16", "resolution": "1080p", "style": "", "voice": "en-us",
    "slow_voice": False, "music": "", "music_volume": 0.12, "transition": "crossfade",
    "motion": "t2v", "review": True,
    "subtitles": {"enabled": True, "size": "medium", "position": "bottom", "color": "white", "words": 7},
}


def scene_count(duration):
    return max(3, min(14, round(int(duration) / 5)))


def build_config(video_type, form, defaults=None):
    cfg = dict(DEFAULT_CONFIG)
    cfg["subtitles"] = dict(DEFAULT_CONFIG["subtitles"])
    if defaults:
        cfg.update({k: v for k, v in defaults.items() if k != "subtitles"})
        cfg["subtitles"].update(defaults.get("subtitles", {}))
    spec = MODULES.get(video_type, {})
    for key in ("aspect", "resolution", "style", "voice", "music", "transition", "motion", "category"):
        if form.get(key) not in (None, ""):
            cfg[key] = str(form[key])
    for key in ("duration",):
        if form.get(key):
            cfg[key] = int(form[key])
    if form.get("music_volume") not in (None, ""):
        cfg["music_volume"] = max(0.0, min(1.0, float(form["music_volume"])))
    for key in ("review", "slow_voice"):
        if key in form:
            cfg[key] = str(form[key]).lower() in ("1", "true", "on", "yes")
    subs = form.get("subtitles") or {}
    if isinstance(subs, dict):
        cfg["subtitles"].update({k: v for k, v in subs.items() if v not in (None, "")})
    if spec:
        if cfg["style"] not in spec["styles"]:
            cfg["style"] = next(iter(spec["styles"]))
        if cfg["duration"] not in spec["durations"]:
            cfg["duration"] = spec["durations"][min(1, len(spec["durations"]) - 1)]
    if cfg["aspect"] not in ASPECTS:
        cfg["aspect"] = "9:16"
    if cfg["resolution"] not in RESOLUTIONS:
        cfg["resolution"] = "1080p"
    if cfg["voice"] not in VOICES:
        cfg["voice"] = "en-us"
    if cfg["transition"] not in TRANSITIONS:
        cfg["transition"] = "crossfade"
    if cfg["motion"] not in MOTION_MODES:
        cfg["motion"] = "t2v"
    cfg["headings"] = video_type == "answer" or bool(form.get("headings"))
    cfg.setdefault("seed", random.randint(1, 2 ** 31 - 1))
    return cfg


# ------------------------------------------------------------- planning
def _language_rule(cfg):
    v = VOICES.get(cfg.get("voice"), VOICES["en-us"])
    if v["lang"] == "en":
        return "Write the narration in English."
    return f"Write the narration in {v['label']}. Keep 'visual' prompts in English."


def plan_prompt(video_type, prompt, cfg):
    spec = MODULES[video_type]
    n = scene_count(cfg["duration"])
    words = int(cfg["duration"] * 2.4)
    extra = ""
    if video_type == "story":
        cat = spec["categories"].get(cfg.get("category", "entertainment"), "General entertainment")
        extra = (f"Story category: {cat}.\nStructure: scene 1 is the introduction, the middle scenes are the "
                 f"main story, the last scene is the conclusion{' with the moral' if cfg.get('category') == 'moral' else ''}.\n"
                 "Create 1-3 named characters and describe their fixed appearance in 'characters'.")
    elif video_type == "animation":
        extra = ("Create 1-3 animated characters with distinctive, fixed appearance (colors, clothing, shape) "
                 "described in 'characters'. Each 'visual' should describe character movement, expression and "
                 "the animated background.")
    elif video_type == "answer":
        extra = ("First understand the question, then give an accurate, concise answer. Split the answer into "
                 "logical sections: a hook, a definition, 1-3 key points or an example, and a one-line summary. "
                 "Each scene needs a short 'heading' (max 5 words) and 2-3 important 'keywords' that appear "
                 "verbatim in its narration. Be factually correct; do not invent statistics.")
    elif video_type == "short":
        extra = "Open with a strong hook in scene 1 and end with a call to action."
    return f"""You are a professional video writer. Create {spec['intro']}.

Topic / input:
{prompt}

{extra}
{_language_rule(cfg)}
Total narration: about {words} words across exactly {n} scenes (each narration 1-2 short sentences).

Return ONLY a JSON object with this exact shape:
{{
  "title": "catchy title, max 70 characters",
  "description": "2-3 sentence description for social platforms",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
  "characters": "fixed visual description of recurring characters, or empty string",
  "scenes": [
    {{"narration": "spoken text", "visual": "concrete visual description of what is on screen: subject, action, setting, camera movement (English, 15-30 words)", "heading": "short on-screen heading", "keywords": ["word1", "word2"]}}
  ]
}}"""


def normalise_plan(data, video_type, n_expected=None):
    if isinstance(data, list):
        data = {"scenes": data}
    scenes = []
    for i, s in enumerate(data.get("scenes") or []):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or s.get("text") or "").strip()
        if not narration:
            continue
        kws = s.get("keywords") or []
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",")]
        scenes.append({
            "narration": narration,
            "visual": str(s.get("visual") or s.get("keyword") or narration).strip(),
            "heading": str(s.get("heading") or "").strip()[:60],
            "keywords": [str(k).strip() for k in kws if str(k).strip()][:4],
        })
    if not scenes:
        raise llm.LLMError("The language model returned no usable scenes. Try regenerating.")
    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    return {
        "title": str(data.get("title") or "").strip().strip('"')[:100],
        "description": str(data.get("description") or "").strip(),
        "hashtags": [t if str(t).startswith("#") else f"#{t}" for t in tags][:10],
        "characters": str(data.get("characters") or "").strip(),
        "scenes": scenes,
    }


def generate_plan(video_type, prompt, cfg):
    return normalise_plan(llm.chat_json(plan_prompt(video_type, prompt, cfg)), video_type)


def generate_news_plan(article, cfg):
    """Review-mode news: reuses the original news prompts verbatim."""
    from . import legacy
    fmt = {"9:16": "short", "1:1": "instagram"}.get(cfg.get("aspect"), "youtube")
    title = legacy.generate_title(article)
    script = legacy.generate_script(article, fmt)
    scenes = legacy.detect_scenes(script)
    description = legacy.generate_description(article)
    return {
        "title": title, "description": description, "hashtags": [], "characters": "",
        "scenes": [{"narration": s["text"], "visual": s["keyword"], "heading": "", "keywords": []}
                   for s in scenes if s.get("text")],
    }


# --------------------------------------------------------------- visuals
def visual_prompt(video_type, scene, cfg, plan):
    styles = MODULES.get(video_type, {}).get("styles", {})
    style = styles.get(cfg.get("style"), "")
    if video_type == "news":
        style = "professional news photography, photojournalism, realistic"
    parts = [style, scene.get("visual", "")]
    if plan.get("characters"):
        parts.append(f"characters: {plan['characters']}")
    parts.append("high detail, coherent composition")
    return ", ".join(p for p in parts if p)


def keyframe_size(aspect):
    return {"9:16": (512, 768), "1:1": (512, 512)}.get(aspect, (768, 512))


def generate_keyframe(video_type, scene, idx, cfg, plan, workdir):
    w, h = keyframe_size(cfg.get("aspect"))
    out = os.path.join(workdir, f"keyframe_{idx}.png")
    models.generate_image(visual_prompt(video_type, scene, cfg, plan), out, width=w, height=h,
                          negative=NEGATIVE, seed=cfg.get("seed", 42) + idx)
    return out


def generate_scene_visual(video_type, scene, idx, cfg, plan, workdir, log_fn):
    """Populate scene['clip'] (AI video) or scene['image'] (still). Mutates and returns scene."""
    motion = cfg.get("motion", "t2v")
    seed = cfg.get("seed", 42) + idx
    prompt = visual_prompt(video_type, scene, cfg, plan)
    portrait = cfg.get("aspect") == "9:16"
    source_image = scene.get("input_image") or scene.get("image")

    if motion == "i2v" or (source_image and scene.get("input_image") and motion != "none"):
        if not source_image:
            source_image = generate_keyframe(video_type, scene, idx, cfg, plan, workdir)
            scene["image"] = source_image
        if models.svd_model_id():
            out = os.path.join(workdir, f"clip_{idx}.mp4")
            models.animate_image(source_image, out, seed=seed, portrait=portrait)
            scene.update(clip=out, visual_kind="ai-video (image-to-video)")
            return scene
        if motion == "i2v":
            raise RuntimeError("Image-to-video needs Stable Video Diffusion. Configure it in Settings → AI models, "
                               "or choose text-to-video.")
        # Uploaded image but SVD not configured: keep the user's image, animate with camera motion.
        log_fn(f"Scene {idx + 1}: Stable Video Diffusion not configured — uploaded image uses camera motion")
        scene.update(image=source_image, visual_kind="uploaded image (camera motion)")
        return scene

    if motion in ("t2v", "i2v"):
        out = os.path.join(workdir, f"clip_{idx}.mp4")
        try:
            models.generate_video_clip(prompt, out, negative=NEGATIVE, seed=seed, aspect=cfg.get("aspect"))
            scene.update(clip=out, visual_kind=f"ai-video ({models.video_engine()})")
            return scene
        except Exception as exc:
            log_fn(f"⚠ Scene {idx + 1}: AI video generation failed ({exc}); using AI keyframe with camera motion")
            log.exception("video clip failed")

    if not scene.get("image") or not os.path.exists(scene["image"]):
        scene["image"] = generate_keyframe(video_type, scene, idx, cfg, plan, workdir)
    scene["visual_kind"] = "ai-image (camera motion)"
    return scene
