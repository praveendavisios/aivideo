"""JSON API used by the dashboard UI."""
import os
import re
from datetime import datetime, timedelta, timezone

import feedparser
from flask import Blueprint, jsonify, request, send_file

from .. import auth, capabilities, db, jobs, media, repo, storage
from ..config import Config
from ..pipelines import models, render, studio

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.before_request
@auth.login_required
def _guard():
    return None


def err(message, code=400, **extra):
    return jsonify(error=message, **extra), code


def _video_or_404(video_id):
    v = repo.get_video(video_id)
    if not v:
        return None, err("Video not found", 404)
    return v, None


def public_video(v):
    """Strip absolute server paths before sending a video to the browser."""
    if not v:
        return v
    out = {k: v[k] for k in v if k not in ("file_path", "thumbnail_path")}
    out["has_file"] = bool(v.get("file_path") and storage.resolve_safe(v["file_path"])
                           and os.path.exists(storage.resolve_safe(v["file_path"])))
    out["has_thumbnail"] = bool(v.get("thumbnail_path") and os.path.exists(v["thumbnail_path"]))
    cfg = dict(v.get("config") or {})
    cfg.pop("input_images", None)
    out["config"] = cfg
    script = v.get("script")
    if isinstance(script, dict):
        s = dict(script)
        s["scenes"] = [{
            **{k: sc.get(k) for k in ("narration", "visual", "heading", "keywords", "visual_kind", "image_source")},
            "has_image": bool(sc.get("image") and os.path.exists(sc["image"])),
            "has_clip": bool(sc.get("clip") and os.path.exists(sc["clip"])),
            "has_input_image": bool(sc.get("input_image")),
            "v": int(os.path.getmtime(sc["image"])) if sc.get("image") and os.path.exists(sc["image"]) else 0,
        } for sc in script.get("scenes", [])]
        out["script"] = s
    return out


def _submit(kind, video_id, params=None, summary="", parent=None):
    try:
        return jobs.submit(kind, video_id, params or {}, summary, parent_job_id=parent), None
    except jobs.DuplicateJob as d:
        return None, err(str(d), 409, job_id=d.job["id"])


def _has_active_job(video_id):
    return db.query(f"SELECT id FROM jobs WHERE video_id=? AND status IN {repo.JOB_ACTIVE}", (video_id,), one=True)


# ------------------------------------------------------------ dashboard
@bp.get("/stats")
def stats():
    recent = repo.list_jobs(per_page=8)["items"]
    uploads = repo.list_videos("uploaded", per_page=5)["items"]
    completed = repo.list_videos("completed", sort="updated", per_page=5)["items"]
    return jsonify(
        stats=repo.dashboard_stats(), job_status=repo.job_status_counts(), daily=repo.daily_counts(14),
        types=repo.type_counts(), recent_jobs=recent,
        latest_uploads=[public_video(v) for v in uploads], recently_completed=[public_video(v) for v in completed],
        active_jobs=len(repo.list_jobs(active_only=True, per_page=100)["items"]),
    )


@bp.get("/capabilities")
def caps():
    return jsonify(capabilities.detect(force=request.args.get("refresh") == "1"))


# --------------------------------------------------------------- studio
def _form_config(video_type):
    f = request.form
    form = {k: f.get(k) for k in ("aspect", "resolution", "style", "voice", "music", "music_volume",
                                  "transition", "motion", "duration", "category", "review", "slow_voice",
                                  "headings")}
    form["review"] = f.get("review", "0")
    form["slow_voice"] = f.get("slow_voice", "0")
    form["subtitles"] = {
        "enabled": f.get("subtitles_enabled", "1") in ("1", "true", "on"),
        "size": f.get("subtitles_size") or None, "position": f.get("subtitles_position") or None,
        "color": f.get("subtitles_color") or None,
    }
    if form["subtitles"]["color"] and not re.fullmatch(r"#[0-9a-fA-F]{6}|white|yellow", form["subtitles"]["color"]):
        form["subtitles"]["color"] = "white"
    return studio.build_config(video_type, form, repo.get_setting("defaults", {}))


def _save_input_images():
    paths = []
    for f in request.files.getlist("images")[:8]:
        if not f or not f.filename:
            continue
        ext = storage.ext_of(f.filename)
        if ext not in Config.ALLOWED_IMAGE_EXT:
            raise ValueError(f"{f.filename}: unsupported image type")
        dest = storage.path(storage.INPUTS, storage.unique_name(ext))
        f.save(dest)
        try:
            from PIL import Image
            with Image.open(dest) as im:
                im.verify()
        except Exception:
            os.remove(dest)
            raise ValueError(f"{f.filename}: not a valid image")
        paths.append(dest)
    return paths


@bp.post("/studio/<video_type>")
def studio_create(video_type):
    if video_type not in studio.MODULES:
        return err("Unknown studio module", 404)
    prompt = (request.form.get("prompt") or "").strip()
    if len(prompt) < 3:
        return err("Please enter a prompt (at least 3 characters).")
    if len(prompt) > 4000:
        return err("Prompt is too long (max 4000 characters).")
    cfg = _form_config(video_type)
    if cfg["motion"] == "i2v" and not models.svd_model_id():
        return err("Image-to-video needs Stable Video Diffusion — configure it in Settings → AI models.")
    since = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(microsecond=0).isoformat()
    dup = db.query("SELECT id FROM videos WHERE video_type=? AND prompt=? AND created_at>=? AND status IN "
                   "('scripting','queued','processing')", (video_type, prompt, since), one=True)
    if dup:
        return err("This request is already being processed.", 409, video_id=dup["id"])
    try:
        cfg["input_images"] = _save_input_images() if studio.MODULES[video_type].get("image_input") else []
    except ValueError as exc:
        return err(str(exc))
    video_id = repo.create_video(video_type, "ai", prompt=prompt, config=cfg, status="scripting",
                                 category=cfg.get("category", ""))
    job_id, e = _submit("script", video_id, {}, prompt[:200])
    if e:
        return e
    render_job = None
    if not cfg.get("review"):
        render_job, e = _submit("render", video_id, {}, prompt[:200], parent=job_id)
    return jsonify(video_id=video_id, job_id=job_id, render_job_id=render_job), 201


# --------------------------------------------------------------- videos
@bp.get("/videos")
def videos_list():
    a = request.args
    res = repo.list_videos(a.get("filter", "all"), a.get("q", "").strip(), a.get("sort", "newest"),
                           a.get("page", 1), a.get("per_page", 12), video_type=a.get("type") or None)
    res["items"] = [public_video(v) for v in res["items"]]
    return jsonify(res)


@bp.get("/videos/<int:video_id>")
def video_get(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    return jsonify(video=public_video(v), jobs=repo.list_jobs(video_id=video_id, per_page=10)["items"])


@bp.patch("/videos/<int:video_id>")
def video_update(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    data = request.get_json(silent=True) or {}
    fields = {}
    for k, limit in (("title", 200), ("description", 5000), ("tags", 500), ("category", 100)):
        if k in data:
            fields[k] = str(data[k] or "").strip()[:limit]
    if "title" in fields and not fields["title"]:
        return err("Title cannot be empty")
    repo.update_video(video_id, **fields)
    return jsonify(video=public_video(repo.get_video(video_id)))


@bp.put("/videos/<int:video_id>/script")
def video_script_save(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    if v["source_type"] != "ai" or not v.get("script"):
        return err("This video has no editable script")
    if _has_active_job(video_id):
        return err("Wait for the running job to finish before editing.", 409)
    data = request.get_json(silent=True) or {}
    old = v["script"]
    old_scenes = old.get("scenes", [])
    new_scenes = []
    for item in (data.get("scenes") or [])[:30]:
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue
        src = item.get("source_index")
        base = dict(old_scenes[src]) if isinstance(src, int) and 0 <= src < len(old_scenes) else {}
        visual = str(item.get("visual") or "").strip()[:600] or narration
        if base and base.get("visual") != visual:
            base.pop("clip", None)  # visual prompt changed → regenerate its AI video
            if not base.get("input_image"):
                base.pop("image", None)
        base.update(narration=narration[:1000], visual=visual,
                    heading=str(item.get("heading") or "").strip()[:60],
                    keywords=[str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()][:4])
        new_scenes.append(base)
    if not new_scenes:
        return err("At least one scene with narration is required")
    old.update(scenes=new_scenes, characters=str(data.get("characters", old.get("characters", "")))[:1000])
    fields = {"script": old}
    if data.get("title"):
        fields["title"] = str(data["title"]).strip()[:200]
    if "description" in data:
        fields["description"] = str(data["description"]).strip()[:5000]
    repo.update_video(video_id, **fields)
    return jsonify(video=public_video(repo.get_video(video_id)))


@bp.post("/videos/<int:video_id>/script/regenerate")
def video_script_regen(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    if v["source_type"] != "ai" or (v.get("config") or {}).get("mode") == "quick":
        return err("Script generation is not available for this video")
    if _has_active_job(video_id):
        return err("Wait for the running job to finish first.", 409)
    repo.update_video(video_id, status="scripting", error=None)
    job_id, e = _submit("script", video_id, {"n": datetime.now().timestamp()}, v["prompt"][:200])
    return e or jsonify(job_id=job_id)


@bp.post("/videos/<int:video_id>/storyboard")
def video_storyboard(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    if not v.get("script"):
        return err("Generate the script first")
    data = request.get_json(silent=True) or {}
    params = {}
    if data.get("scene") is not None:
        idx = int(data["scene"])
        if not 0 <= idx < len(v["script"]["scenes"]):
            return err("Invalid scene")
        params["scene"] = idx
    job_id, e = _submit("storyboard", video_id, params, f"Storyboard: {v['title'] or v['prompt'][:120]}")
    if not e:
        repo.update_video(video_id, status="storyboarding")
    return e or jsonify(job_id=job_id)


def _queue_render(v, regenerate=False):
    if v["source_type"] == "upload":
        return None, err("Uploaded videos cannot be regenerated")
    cfg = v.get("config") or {}
    if v["video_type"] == "news" and cfg.get("mode") == "quick":
        kind, params = "news", {}
    else:
        if not v.get("script"):
            return None, err("Generate the script first")
        kind, params = "render", ({"regenerate_visuals": True} if regenerate else {})
    job_id, e = _submit(kind, v["id"], params, v["title"] or v["prompt"][:200])
    if not e:
        repo.update_video(v["id"], status="queued", error=None)
    return job_id, e


@bp.post("/videos/<int:video_id>/render")
def video_render(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    job_id, e = _queue_render(v, bool((request.get_json(silent=True) or {}).get("regenerate_visuals")))
    return e or jsonify(job_id=job_id)


@bp.post("/videos/<int:video_id>/regenerate")
def video_regenerate(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    job_id, e = _queue_render(v, regenerate=True)
    return e or jsonify(job_id=job_id)


@bp.post("/videos/<int:video_id>/publish")
def video_publish(video_id):
    from ..publishing import youtube
    v, e = _video_or_404(video_id)
    if e:
        return e
    if v["status"] != "completed" or not v.get("file_path"):
        return err("Only completed videos can be published")
    if not youtube.is_configured():
        return err("YouTube is not connected (youtube_token.json not found).")
    d = request.get_json(silent=True) or {}
    privacy = d.get("privacy", "public")
    if privacy not in ("public", "unlisted", "private"):
        return err("Invalid privacy option")
    params = {"title": str(d.get("title") or v["title"])[:100], "description": str(d.get("description") or v["description"]),
              "tags": str(d.get("tags") or v["tags"] or ""), "privacy": privacy, "shorts": bool(d.get("shorts", True)),
              "platform": "youtube"}
    job_id, e = _submit("publish", video_id, params, f"Publish: {params['title']}")
    if not e:
        repo.update_video(video_id, publish_status="publishing")
    return e or jsonify(job_id=job_id)


@bp.delete("/videos/<int:video_id>")
def video_delete(video_id):
    v, e = _video_or_404(video_id)
    if e:
        return e
    if _has_active_job(video_id):
        return err("This video has a running job. Cancel it first.", 409)
    for p in (v.get("file_path"), v.get("thumbnail_path")):
        storage.remove_file(p)
    for p in (v.get("config") or {}).get("input_images") or []:
        storage.remove_file(p)
    storage.remove_tree(storage.job_workdir(video_id))
    repo.delete_video(video_id)
    return jsonify(ok=True)


# ----------------------------------------------------------------- jobs
@bp.get("/jobs")
def jobs_list():
    a = request.args
    return jsonify(repo.list_jobs(a.get("status") or None, a.get("kind") or None, a.get("q", "").strip(),
                                  a.get("page", 1), a.get("per_page", 20), active_only=a.get("active") == "1"))


@bp.get("/jobs/<int:job_id>")
def job_get(job_id):
    j = repo.get_job(job_id)
    if not j:
        return err("Job not found", 404)
    j.pop("dedupe_key", None)
    return jsonify(job=j)


@bp.post("/jobs/<int:job_id>/cancel")
def job_cancel(job_id):
    j = jobs.cancel(job_id)
    return jsonify(job=j) if j else err("Job not found", 404)


@bp.post("/jobs/<int:job_id>/retry")
def job_retry(job_id):
    try:
        return jsonify(job_id=jobs.retry(job_id))
    except jobs.DuplicateJob as d:
        return err(str(d), 409, job_id=d.job["id"])
    except ValueError as exc:
        return err(str(exc))


# ----------------------------------------------------------------- news
NEWS_CATEGORIES = ["world", "technology", "business", "sports", "science", "politics", "economy", "finance",
                   "health", "education", "environment", "climate", "entertainment", "movies", "music",
                   "celebrity", "gaming", "ai", "startups", "crime", "law", "military", "space", "innovation"]


@bp.get("/news")
def news_feed():
    """Same Google News RSS source as the original /news, with more fields."""
    category = request.args.get("category", "world")
    if category not in NEWS_CATEGORIES:
        return err("Unknown category")
    count = min(max(int(request.args.get("count", 10)), 1), 50)
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={category}&hl=en-US&gl=US&ceid=US:en")
    if getattr(feed, "bozo", False) and not feed.entries:
        return err("Could not fetch the news feed. Check the server's internet connection.", 502)
    items = [{"title": e.get("title", ""), "link": e.get("link", ""), "published": e.get("published", ""),
              "source": (e.get("source") or {}).get("title", "")} for e in feed.entries[:count]]
    return jsonify(items=items, category=category)


@bp.post("/news/generate")
def news_generate():
    d = request.get_json(silent=True) or {}
    articles = [str(a).strip() for a in (d.get("articles") or []) if str(a).strip()][:10]
    if not articles:
        return err("Select at least one article")
    mode = d.get("mode", "quick")
    fmt = d.get("format", "short")
    if fmt not in ("short", "instagram", "youtube"):
        return err("Invalid format")
    aspect = {"short": "9:16", "instagram": "1:1", "youtube": "16:9"}[fmt]
    created = []
    for article in articles:
        if mode == "quick":
            cfg = {"mode": "quick", "format": fmt, "ai_only": bool(d.get("ai_only")), "aspect": aspect}
            vid = repo.create_video("news", "ai", prompt=article, title=article[:120], config=cfg, status="queued",
                                    category=d.get("category", ""))
            job_id, e = _submit("news", vid, {}, article)
        else:
            form = dict(d.get("config") or {})
            form["aspect"] = aspect
            cfg = studio.build_config("news", form, repo.get_setting("defaults", {}))
            cfg.update(mode="review", format=fmt, ai_only=bool(d.get("ai_only")), review=True,
                       motion=form.get("motion") or "none")
            vid = repo.create_video("news", "ai", prompt=article, title="", config=cfg, status="scripting",
                                    category=d.get("category", ""))
            job_id, e = _submit("script", vid, {}, article)
        if e:
            repo.delete_video(vid)
            continue
        created.append({"video_id": vid, "job_id": job_id})
    if not created:
        return err("These articles are already being processed.", 409)
    return jsonify(created=created), 201


# --------------------------------------------------------------- upload
@bp.post("/uploads")
def upload_video():
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No file received")
    ext = storage.ext_of(f.filename)
    if ext not in Config.ALLOWED_VIDEO_EXT:
        return err(f"Unsupported format .{ext}. Allowed: {', '.join(sorted(Config.ALLOWED_VIDEO_EXT))}", 415)
    dest = storage.path(storage.UPLOADS, storage.unique_name(ext))
    f.save(dest)
    try:
        info = media.describe(dest)
    except Exception as exc:
        storage.remove_file(dest)
        return err(f"The file is not a playable video ({exc.__class__.__name__}).", 422)
    title = (request.form.get("title") or os.path.splitext(f.filename)[0]).strip()[:200]
    vid = repo.create_video(
        "upload", "upload", title=title, description=(request.form.get("description") or "").strip()[:5000],
        status="completed", file_path=dest, tags=(request.form.get("tags") or "").strip()[:500],
        category=(request.form.get("category") or "").strip()[:100], completed_at=db.now_iso(),
        config={"original_filename": os.path.basename(f.filename)},
        **{k: info.get(k) for k in ("duration", "width", "height", "file_size", "thumbnail_path")})
    return jsonify(video=public_video(repo.get_video(vid))), 201


# ---------------------------------------------------------------- music
@bp.get("/music")
def music_list():
    return jsonify(items=storage.list_music())


@bp.post("/music")
def music_upload():
    f = request.files.get("file")
    if not f or storage.ext_of(f.filename or "") not in Config.ALLOWED_AUDIO_EXT:
        return err("Upload an audio file (mp3, wav, m4a, aac, ogg)")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(f.filename))[:120]
    f.save(os.path.join(storage.path(storage.MUSIC), name))
    return jsonify(items=storage.list_music()), 201


@bp.delete("/music/<name>")
def music_delete(name):
    p = storage.music_path(name)
    if not p:
        return err("Track not found", 404)
    os.remove(p)
    return jsonify(items=storage.list_music())


# ------------------------------------------------------------- settings
MODEL_KEYS = {"ollama": "Ollama model", "ollama_host": "Ollama host", "sd": "Stable Diffusion model",
              "animatediff": "AnimateDiff motion adapter", "video_engine": "Text-to-video engine",
              "wan": "Wan model", "svd": "Stable Video Diffusion model"}


def _settings_payload():
    from ..publishing import youtube
    return {
        "profile": {"username": Config.ADMIN_USERNAME, "credentials_source": auth.credentials_source(),
                    "session_hours": Config.SESSION_HOURS, "environment": Config.ENV},
        "models": {k: models.model_setting(k, default) for k, default in (
            ("ollama", Config.OLLAMA_MODEL), ("ollama_host", Config.OLLAMA_HOST), ("sd", Config.SD_MODEL),
            ("animatediff", Config.ANIMATEDIFF_ADAPTER), ("video_engine", "animatediff"),
            ("wan", models.WAN_DEFAULT), ("svd", ""))},
        "video_engines": models.VIDEO_ENGINES,
        "defaults": studio.build_config("short", {}, repo.get_setting("defaults", {})),
        "preferences": repo.get_setting("preferences", {"theme": "system", "page_size": 12}),
        "api": {"pexels": bool(Config.PEXELS_KEY), "pixabay": bool(Config.PIXABAY_KEY)},
        "storage": {"data_dir": Config.DATA_DIR, "legacy_dir": Config.LEGACY_VIDEO_DIR,
                    "used_bytes": storage.disk_usage(), "max_upload_mb": Config.MAX_UPLOAD_MB},
        "publishing": {"youtube": youtube.is_configured(),
                       "youtube_token_file": os.path.basename(Config.YOUTUBE_TOKEN_FILE)},
        "voices": {k: v["label"] for k, v in render.VOICES.items()},
    }


@bp.get("/settings")
def settings_get():
    return jsonify(_settings_payload())


@bp.put("/settings/<section>")
def settings_put(section):
    d = request.get_json(silent=True) or {}
    if section == "models":
        current = repo.get_setting("model_overrides", {})
        for k in MODEL_KEYS:
            if k in d:
                val = str(d[k] or "").strip()[:300]
                if val and not re.fullmatch(r"[A-Za-z0-9_.:/\-]+", val):
                    return err(f"{MODEL_KEYS[k]}: invalid value")
                if k == "video_engine" and val not in models.VIDEO_ENGINES:
                    return err("Unknown video engine")
                repo.set_setting(f"model.{k}", val)
                current[k] = val
        repo.set_setting("model_overrides", current)
        capabilities.detect(force=False)
        capabilities._cache["at"] = 0
    elif section == "defaults":
        cfg = studio.build_config("short", d)
        cfg.pop("seed", None)
        cfg.pop("headings", None)
        repo.set_setting("defaults", cfg)
    elif section == "preferences":
        theme = d.get("theme", "system")
        if theme not in ("system", "light", "dark"):
            return err("Invalid theme")
        repo.set_setting("preferences", {"theme": theme, "page_size": min(max(int(d.get("page_size", 12)), 6), 48)})
    else:
        return err("Unknown settings section", 404)
    return jsonify(_settings_payload())


@bp.post("/settings/password")
def settings_password():
    d = request.get_json(silent=True) or {}
    try:
        auth.change_password(d.get("current", ""), d.get("new", ""))
    except ValueError as exc:
        return err(str(exc))
    return jsonify(ok=True)


@bp.get("/studio-options")
def studio_options():
    return jsonify(
        modules={k: {kk: vv for kk, vv in v.items()} for k, v in studio.MODULES.items()},
        aspects=studio.ASPECTS, resolutions=studio.RESOLUTIONS, transitions=studio.TRANSITIONS,
        motion=studio.MOTION_MODES, voices={k: v["label"] for k, v in render.VOICES.items()},
        music=storage.list_music(), defaults=studio.build_config("short", {}, repo.get_setting("defaults", {})),
        svd_configured=bool(models.svd_model_id()),
    )


# ---------------------------------------------------------------- media
media_bp = Blueprint("media", __name__, url_prefix="/media")


@media_bp.before_request
@auth.login_required
def _media_guard():
    return None


def _send(path, mimetype=None, download_name=None):
    real = storage.resolve_safe(path)
    if not real or not os.path.isfile(real):
        return err("File not found", 404)
    return send_file(real, mimetype=mimetype, conditional=True, as_attachment=bool(download_name),
                     download_name=download_name)


@media_bp.get("/video/<int:video_id>")
def media_video(video_id):
    v = repo.get_video(video_id)
    if not v or not v.get("file_path"):
        return err("Not found", 404)
    ext = storage.ext_of(v["file_path"]) or "mp4"
    name = f"{render.slug(v['title'], 60)}.{ext}" if request.args.get("download") else None
    mime = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "m4v": "video/mp4"}.get(ext)
    return _send(v["file_path"], mime, name)


@media_bp.get("/thumb/<int:video_id>")
def media_thumb(video_id):
    v = repo.get_video(video_id)
    if not v or not v.get("thumbnail_path"):
        return err("Not found", 404)
    return _send(v["thumbnail_path"], "image/jpeg")


@media_bp.get("/scene/<int:video_id>/<int:idx>/<kind>")
def media_scene(video_id, idx, kind):
    v = repo.get_video(video_id)
    scenes = (v or {}).get("script", {}) or {}
    scenes = scenes.get("scenes", []) if isinstance(scenes, dict) else []
    if not 0 <= idx < len(scenes) or kind not in ("image", "clip"):
        return err("Not found", 404)
    return _send(scenes[idx].get(kind), "video/mp4" if kind == "clip" else None)
