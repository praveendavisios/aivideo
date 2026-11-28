"""Original app.py endpoints, preserved for backward compatibility.

/news, /generate, /generate-content, /generate-story, /progress, /video,
/upload_youtube and /open_folder keep their request/response shapes, but
now run through the shared job queue so their output lands in the Video
Library and Generation History. All require login.
"""
import os
import subprocess
import sys
import threading

import feedparser
from flask import Blueprint, jsonify, render_template, request, send_file

from .. import auth, jobs, repo, storage
from ..config import Config
from ..pipelines import studio

bp = Blueprint("legacy", __name__)

_batch = {"job_ids": [], "lock": threading.Lock()}


@bp.before_request
@auth.login_required
@auth.role_required(*auth.STUDIO_ROLES)
def _guard():
    return None


def _start_batch(job_ids):
    with _batch["lock"]:
        _batch["job_ids"] = list(job_ids)


@bp.route("/classic")
def classic():
    return render_template("classic.html")


@bp.route("/news")
def news():
    count = int(request.args.get("count", 3))
    category = request.args.get("category", "world")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={category}&hl=en-US&gl=US&ceid=US:en")
    return jsonify({"news": [e.title for e in feed.entries][:count]})


@bp.route("/generate", methods=["POST"])
def generate():
    data = request.json or {}
    fmt = data.get("format", "short")
    ai_only = data.get("ai_only", False)
    aspect = {"short": "9:16", "instagram": "1:1"}.get(fmt, "16:9")
    ids = []
    for news_item in data.get("news", []):
        vid = repo.create_video("news", "ai", prompt=news_item, title=news_item[:120], status="queued",
                                config={"mode": "quick", "format": fmt, "ai_only": bool(ai_only), "aspect": aspect})
        ids.append(jobs.submit("news", vid, {}, news_item, allow_duplicate=True))
    _start_batch(ids)
    return jsonify({"status": "started"})


def _studio_job(video_type, prompt, fmt, images=None):
    aspect = {"short": "9:16", "instagram": "1:1"}.get(fmt, "16:9")
    cfg = studio.build_config(video_type, {"aspect": aspect, "review": "0", "duration": 45},
                              repo.get_setting("defaults", {}))
    cfg["input_images"] = images or []
    vid = repo.create_video(video_type, "ai", prompt=prompt, config=cfg, status="scripting")
    sj = jobs.submit("script", vid, {}, prompt[:200], allow_duplicate=True)
    rj = jobs.submit("render", vid, {}, prompt[:200], parent_job_id=sj, allow_duplicate=True)
    _start_batch([rj])


@bp.route("/generate-content", methods=["POST"])
def generate_content():
    data = request.json or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"status": "error", "message": "content required"}), 400
    _studio_job("short", content, data.get("format", "short"))
    return {"status": "started"}


@bp.route("/generate-story", methods=["POST"])
def generate_story():
    # The original handler used FastAPI signatures on a Flask app and could
    # not receive files; this is the working Flask equivalent.
    images = []
    for f in request.files.getlist("images")[:8]:
        ext = storage.ext_of(f.filename or "")
        if ext in Config.ALLOWED_IMAGE_EXT:
            dest = storage.path(storage.INPUTS, storage.unique_name(ext))
            f.save(dest)
            images.append(dest)
    if not images:
        return {"status": "error", "message": "Upload at least 1 image"}, 400
    _studio_job("short", "A cinematic story based on the uploaded reference images", request.form.get("format", "short"),
                images)
    return {"status": "started"}


@bp.route("/progress")
def progress():
    ids = list(_batch["job_ids"])
    if not ids:
        return jsonify({"progress": 0, "status": "idle", "videos": [], "news": []})
    js = [repo.get_job(i) for i in ids]
    js = [j for j in js if j]
    done = [j for j in js if j["status"] in ("completed", "failed", "cancelled")]
    running = next((j for j in js if j["status"] == "processing"), None)
    pct = int(sum(100 if j in done else j["progress"] for j in js) / max(len(js), 1))
    if len(done) == len(js):
        status = "completed"
    elif running:
        status = f"Generating video {js.index(running) + 1}/{len(js)} — {running['stage']}"
    else:
        status = "Queued…"
    videos = []
    for j in js:
        if j["status"] != "completed" or not j["video_id"]:
            continue
        v = repo.get_video(j["video_id"])
        if v and v.get("file_path"):
            videos.append({"news": v["prompt"][:200], "title": v["title"], "description": v["description"],
                           "video": v["file_path"], "folder": os.path.dirname(v["file_path"]),
                           "images": (v.get("config") or {}).get("images_used", "AI Video")})
    return jsonify({"progress": pct, "status": status, "videos": videos, "news": []})


@bp.route("/video")
def video():
    path = storage.resolve_safe(request.args.get("path"))
    if not path or not os.path.isfile(path):
        return jsonify(error="File not found"), 404
    return send_file(path, mimetype="video/mp4", conditional=True)


@bp.route("/upload_youtube", methods=["POST"])
def upload_youtube():
    from ..publishing import youtube
    try:
        data = request.json or {}
        path = storage.resolve_safe(data["video"])
        if not path:
            raise ValueError("Video path is outside the media folders")
        res = youtube.upload(path, data["title"], data["description"])
        from .. import db
        for v in db.query("SELECT id, file_path FROM videos WHERE file_path IS NOT NULL"):
            if os.path.realpath(v["file_path"]) == path:
                repo.update_video(v["id"], publish_status="published", publish_url=res["url"])
        return jsonify({"status": "uploaded", **res})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@bp.route("/open_folder")
def open_folder():
    path = storage.resolve_safe(request.args.get("path"))
    if not path or not os.path.isdir(path):
        return "invalid path", 400
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.Popen([opener, path])
    return "ok"
