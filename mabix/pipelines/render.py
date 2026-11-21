"""Shared video compositor used by every AI Studio module.

Input: a list of scenes, each with narration text and a visual (AI motion
clip or still image), plus render options. Output: an MP4 with per-scene
narration, synced captions (with keyword highlighting), transitions and
an optional background-music bed.
"""
import logging
import os
import re

from ..config import BASE_DIR, Config

log = logging.getLogger(__name__)

RESOLUTIONS = {
    "9:16": {"720p": (720, 1280), "1080p": (1080, 1920)},
    "16:9": {"720p": (1280, 720), "1080p": (1920, 1080)},
    "1:1": {"720p": (720, 720), "1080p": (1080, 1080)},
}

VOICES = {
    "en-us": {"label": "English (US)", "lang": "en", "tld": "com"},
    "en-uk": {"label": "English (UK)", "lang": "en", "tld": "co.uk"},
    "en-au": {"label": "English (Australia)", "lang": "en", "tld": "com.au"},
    "en-in": {"label": "English (India)", "lang": "en", "tld": "co.in"},
    "en-ca": {"label": "English (Canada)", "lang": "en", "tld": "ca"},
    "hi": {"label": "Hindi", "lang": "hi", "tld": "co.in"},
    "ml": {"label": "Malayalam", "lang": "ml", "tld": "co.in"},
    "ta": {"label": "Tamil", "lang": "ta", "tld": "co.in"},
    "es": {"label": "Spanish", "lang": "es", "tld": "com"},
    "fr": {"label": "French", "lang": "fr", "tld": "fr"},
    "de": {"label": "German", "lang": "de", "tld": "de"},
}

TRANSITION_SECONDS = 0.5
SCENE_PAD = 0.6  # silence after each narration line


class Cancelled(Exception):
    pass


def resolve_font():
    for candidate in (
        Config.FONT_PATH,
        os.path.join(BASE_DIR, "ttfFile", "DejaVuSans-Bold.ttf"),  # original app font
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def frame_size(aspect, resolution):
    return RESOLUTIONS.get(aspect, RESOLUTIONS["9:16"]).get(resolution, RESOLUTIONS["9:16"]["1080p"])


def synthesize(text, out_path, voice="en-us", slow=False):
    """gTTS narration (same engine as the original app)."""
    from gtts import gTTS
    v = VOICES.get(voice, VOICES["en-us"])
    gTTS(text, lang=v["lang"], tld=v["tld"], slow=bool(slow)).save(out_path)
    return out_path


def caption_chunks(text, max_words=7):
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)] or [""]


def _cover(clip, w, h):
    """Scale and center-crop a clip to exactly fill w×h."""
    scale = max(w / clip.w, h / clip.h)
    clip = clip.resized(scale)
    return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=w, height=h)


def _visual_clip(scene, w, h, duration):
    from moviepy import CompositeVideoClip, ImageClip, VideoFileClip, vfx

    clip_path, image_path = scene.get("clip"), scene.get("image")
    if clip_path and os.path.exists(clip_path):
        clip = VideoFileClip(clip_path, audio=False)
        clip = _cover(clip, w, h)
        if clip.duration < duration:
            # Slow slightly (down to 0.7x) before falling back to a loop, which looks less repetitive.
            speed = max(0.7, clip.duration / duration)
            if speed < 0.999:
                clip = clip.with_effects([vfx.MultiplySpeed(speed)])
            if clip.duration < duration - 0.05:
                clip = clip.with_effects([vfx.Loop(duration=duration)])
        return clip.with_duration(duration)
    if image_path and os.path.exists(image_path):
        # Ken Burns motion for still images (labelled fallback).
        img = _cover(ImageClip(image_path), w, h).with_duration(duration)
        zoom = img.resized(lambda t: 1 + 0.06 * t / max(duration, 0.1)).with_position("center")
        return CompositeVideoClip([zoom], size=(w, h)).with_duration(duration)
    raise RuntimeError(f"Scene {scene.get('index', '?')} has no generated visual")


def _text(text, size, color, w, font, bg=None, stroke=True):
    from moviepy import TextClip
    kw = dict(text=text, font_size=size, color=color, size=(int(w), None), method="caption",
              text_align="center", margin=(16, 12))
    if font:
        kw["font"] = font
    if stroke:
        kw.update(stroke_color="black", stroke_width=max(2, size // 18))
    if bg:
        kw["bg_color"] = bg
    return TextClip(**kw)


def _scene_overlays(scene, w, h, duration, opts, font):
    subs = opts.get("subtitles", {})
    layers = []
    heading = (scene.get("heading") or "").strip()
    if opts.get("headings") and heading:
        chip = _text(heading, int(h * 0.032), "black", w * 0.8, font, bg=opts.get("highlight_color", "#FFD84D"),
                     stroke=False)
        layers.append(chip.with_position(("center", int(h * 0.08))).with_duration(duration))
    if not subs.get("enabled", True):
        return layers
    narration = scene.get("narration", "")
    chunks = caption_chunks(narration, int(subs.get("words", 7)))
    keywords = [k.lower() for k in scene.get("keywords", []) if k]
    total_chars = sum(len(c) for c in chunks) or 1
    speech = max(0.5, duration - SCENE_PAD)
    size = int(h * {"small": 0.026, "medium": 0.034, "large": 0.044}.get(subs.get("size", "medium"), 0.034))
    pos = subs.get("position", "bottom")
    t = 0.0
    for chunk in chunks:
        d = speech * len(chunk) / total_chars
        hit = any(k in chunk.lower() for k in keywords)
        color = opts.get("highlight_color", "#FFD84D") if hit else subs.get("color", "white")
        txt = _text(chunk, size, color, w * 0.86, font)
        y = {"top": int(h * 0.18), "center": "center"}.get(pos, int(h * 0.74))
        layers.append(txt.with_position(("center", y)).with_start(t).with_duration(d))
        t += d
    return layers


def render(scenes, opts, workdir, out_path, log_fn=print, cancel_check=lambda: None, progress_fn=lambda p: None):
    """Compose scenes into the final MP4. Returns out_path."""
    from moviepy import (AudioFileClip, CompositeAudioClip, CompositeVideoClip, afx,
                         concatenate_videoclips, vfx)

    w, h = frame_size(opts.get("aspect", "9:16"), opts.get("resolution", "1080p"))
    font = resolve_font()
    transition = opts.get("transition", "crossfade")
    clips = []
    n = len(scenes)
    for i, scene in enumerate(scenes):
        cancel_check()
        narration = scene.get("narration", "").strip()
        if not narration:
            raise RuntimeError(f"Scene {i + 1} has no narration text")
        voice_path = scene.get("voice")
        if not (voice_path and os.path.exists(voice_path) and scene.get("voice_text") == narration):
            voice_path = os.path.join(workdir, f"voice_{i}.mp3")
            synthesize(narration, voice_path, opts.get("voice", "en-us"), opts.get("slow_voice"))
        voice = AudioFileClip(voice_path)
        duration = voice.duration + SCENE_PAD
        visual = _visual_clip(scene, w, h, duration)
        layers = [visual] + _scene_overlays(scene, w, h, duration, opts, font)
        sc = CompositeVideoClip(layers, size=(w, h)).with_duration(duration).with_audio(voice)
        if transition == "crossfade" and i > 0:
            sc = sc.with_effects([vfx.CrossFadeIn(TRANSITION_SECONDS)])
        elif transition == "fade":
            sc = sc.with_effects([vfx.FadeIn(TRANSITION_SECONDS / 2), vfx.FadeOut(TRANSITION_SECONDS / 2)])
        clips.append(sc)
        log_fn(f"Composed scene {i + 1}/{n} ({duration:.1f}s)")
        progress_fn((i + 1) / n)

    padding = -TRANSITION_SECONDS if transition == "crossfade" and len(clips) > 1 else 0
    video = concatenate_videoclips(clips, method="compose", padding=padding)

    music = opts.get("music_path")
    if music and os.path.exists(music):
        bed = AudioFileClip(music).with_effects([
            afx.AudioLoop(duration=video.duration),
            afx.MultiplyVolume(float(opts.get("music_volume", 0.12))),
            afx.AudioFadeOut(min(2.0, video.duration / 4)),
        ])
        video = video.with_audio(CompositeAudioClip([video.audio, bed]).with_duration(video.duration))
        log_fn(f"Mixed background music: {os.path.basename(music)}")

    cancel_check()
    log_fn(f"Encoding {w}x{h} MP4…")
    video.write_videofile(out_path, fps=int(opts.get("fps", 30)), codec="libx264", audio_codec="aac",
                          threads=os.cpu_count(), preset="medium", logger=None,
                          ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
                          temp_audiofile=os.path.join(workdir, "temp_audio.m4a"))
    video.close()
    return out_path


def slug(text, limit=40):
    s = re.sub(r"[^a-zA-Z0-9 ]", "", text or "video")[:limit].strip().replace(" ", "_")
    return s or "video"


def prepare_narration(scenes, opts, workdir):
    """Synthesize narration first so each AI clip can be generated to match its scene length."""
    from moviepy import AudioFileClip
    for i, scene in enumerate(scenes):
        text = scene.get("narration", "").strip()
        if not text:
            continue
        path = scene.get("voice")
        if not (path and os.path.exists(path) and scene.get("voice_text") == text):
            path = os.path.join(workdir, f"voice_{i}.mp3")
            synthesize(text, path, opts.get("voice", "en-us"), opts.get("slow_voice"))
        a = AudioFileClip(path)
        scene.update(voice=path, voice_text=text, voice_duration=round(a.duration + SCENE_PAD, 2))
        a.close()
