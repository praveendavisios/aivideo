"""File-system layout and path safety for all media."""
import os
import shutil
import uuid

from .config import Config

UPLOADS = "uploads"
GENERATED = "generated"
THUMBS = "thumbnails"
MUSIC = "music"
WORK = "work"
INPUTS = "inputs"


def root():
    return Config.DATA_DIR


def path(*parts):
    p = os.path.join(Config.DATA_DIR, *parts)
    os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


def ensure_dirs():
    for d in (UPLOADS, GENERATED, THUMBS, MUSIC, WORK, INPUTS):
        os.makedirs(os.path.join(Config.DATA_DIR, d), exist_ok=True)
    os.makedirs(Config.LEGACY_VIDEO_DIR, exist_ok=True)


def job_workdir(video_id):
    return path(WORK, f"video_{video_id}")


def allowed_roots():
    return [os.path.realpath(Config.DATA_DIR), os.path.realpath(Config.LEGACY_VIDEO_DIR)]


def resolve_safe(p):
    """Return an absolute real path only if it lives inside a storage root."""
    if not p:
        return None
    from .config import BASE_DIR
    full = p if os.path.isabs(p) else os.path.join(BASE_DIR, p)
    real = os.path.realpath(full)
    for r in allowed_roots():
        if real == r or real.startswith(r + os.sep):
            return real
    return None


def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def unique_name(ext):
    return f"{uuid.uuid4().hex}.{ext}"


def remove_file(p):
    real = resolve_safe(p)
    if real and os.path.isfile(real):
        os.remove(real)


def remove_tree(p):
    real = resolve_safe(p)
    if real and os.path.isdir(real) and real not in allowed_roots():
        shutil.rmtree(real, ignore_errors=True)


def disk_usage():
    total = 0
    for base in allowed_roots():
        for dirpath, _, files in os.walk(base):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return total


def list_music():
    d = path(MUSIC)
    out = []
    for f in sorted(os.listdir(d)):
        if ext_of(f) in Config.ALLOWED_AUDIO_EXT:
            out.append({"name": f, "size": os.path.getsize(os.path.join(d, f))})
    return out


def music_path(name):
    if not name:
        return None
    p = os.path.join(path(MUSIC), os.path.basename(name))
    return p if os.path.isfile(p) else None
