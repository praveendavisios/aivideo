"""Video probing and thumbnail extraction (moviepy/ffmpeg)."""
import logging
import os

from . import storage

log = logging.getLogger(__name__)


def probe(video_path):
    """Return duration/width/height/size; raises if the file is not a decodable video."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(video_path, audio=False)
    try:
        info = {
            "duration": round(float(clip.duration or 0), 2),
            "width": int(clip.w),
            "height": int(clip.h),
        }
    finally:
        clip.close()
    info["file_size"] = os.path.getsize(video_path)
    if info["duration"] <= 0:
        raise ValueError("Video has no playable duration")
    return info


def make_thumbnail(video_path, at=1.0):
    from moviepy import VideoFileClip

    out = storage.path(storage.THUMBS, storage.unique_name("jpg"))
    clip = VideoFileClip(video_path, audio=False)
    try:
        t = min(at, max(0, (clip.duration or 0) - 0.05))
        clip.save_frame(out, t=t)
    finally:
        clip.close()
    return out


def describe(video_path):
    """Probe + thumbnail; never raises for the thumbnail step."""
    info = probe(video_path)
    try:
        info["thumbnail_path"] = make_thumbnail(video_path)
    except Exception as exc:  # thumbnail is cosmetic
        log.warning("Thumbnail failed for %s: %s", video_path, exc)
    return info
