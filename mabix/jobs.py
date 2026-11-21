"""Background job queue.

A single worker thread runs jobs one at a time (the GPU is the bottleneck,
and the original pipeline writes shared temp files). Jobs, progress and
logs are persisted in SQLite so the UI can poll them and history survives
restarts.
"""
import logging
import os
import shutil
import threading
import time
import traceback

from . import db, media, repo, storage
from .pipelines.render import Cancelled

log = logging.getLogger(__name__)

_wake = threading.Event()
_worker = None
HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


class JobContext:
    def __init__(self, job):
        self.job = job
        self.id = job["id"]
        self.params = job.get("params") or {}

    def log(self, message):
        log.info("job %s: %s", self.id, message)
        repo.append_job_log(self.id, message)

    def progress(self, pct, stage=None):
        fields = {"progress": max(0, min(100, int(pct)))}
        if stage:
            fields["stage"] = stage
        repo.update_job(self.id, **fields)

    def check_cancel(self):
        row = db.query("SELECT cancel_requested FROM jobs WHERE id=?", (self.id,), one=True)
        if row and row["cancel_requested"]:
            raise Cancelled()


# ----------------------------------------------------------- public API
class DuplicateJob(Exception):
    def __init__(self, job):
        super().__init__(f"An identical job (#{job['id']}) is already {job['status']}")
        self.job = job


def submit(kind, video_id=None, params=None, summary="", parent_job_id=None, retry_of=None, allow_duplicate=False):
    if kind not in HANDLERS:
        raise ValueError(f"Unknown job kind {kind}")
    params = params or {}
    if not allow_duplicate:
        dup = repo.find_active_duplicate(repo.dedupe_key(kind, video_id, params))
        if dup:
            raise DuplicateJob(dup)
    status = "pending" if parent_job_id else "queued"
    job_id = repo.create_job(kind, video_id, params, summary, status, parent_job_id, retry_of)
    _wake.set()
    return job_id


def cancel(job_id):
    job = repo.get_job(job_id)
    if not job:
        return None
    if job["status"] in ("pending", "queued"):
        repo.update_job(job_id, status="cancelled", finished_at=db.now_iso(), stage="Cancelled")
        repo.append_job_log(job_id, "Cancelled before start")
        repo.release_children(job_id, False)
        _restore_video_after_stop(job, "Cancelled")
    elif job["status"] == "processing":
        repo.update_job(job_id, cancel_requested=1, stage="Cancelling…")
        repo.append_job_log(job_id, "Cancellation requested — stops at the next safe point")
    return repo.get_job(job_id)


def retry(job_id):
    job = repo.get_job(job_id)
    if not job or job["status"] not in ("failed", "cancelled"):
        raise ValueError("Only failed or cancelled jobs can be retried")
    new_id = submit(job["kind"], job["video_id"], job["params"], job["summary"], retry_of=job_id)
    _mark_video_queued(job["kind"], job["video_id"])
    return new_id


def _mark_video_queued(kind, video_id):
    if not video_id:
        return
    if kind == "publish":
        repo.update_video(video_id, publish_status="publishing")
    elif kind in ("render", "news"):
        repo.update_video(video_id, status="queued", error=None)
    elif kind == "script":
        repo.update_video(video_id, status="scripting", error=None)


def _restore_video_after_stop(job, reason, failed=False):
    vid = job.get("video_id")
    if not vid:
        return
    video = repo.get_video(vid)
    if not video:
        return
    if job["kind"] == "publish":
        repo.update_video(vid, publish_status="failed" if failed else "unpublished",
                          error=reason if failed else video.get("error"))
    elif failed and job["kind"] != "storyboard":
        repo.update_video(vid, status="failed", error=reason)
    elif video.get("script"):
        repo.update_video(vid, status="review", error=reason if failed else None)
    else:
        repo.update_video(vid, status="failed", error=reason)


# --------------------------------------------------------------- worker
def _run(job):
    ctx = JobContext(job)
    repo.update_job(job["id"], status="processing", started_at=db.now_iso(), stage="Starting…")
    ctx.log(f"Started {job['kind']} job")
    try:
        HANDLERS[job["kind"]](ctx)
        repo.update_job(job["id"], status="completed", progress=100, stage="Completed", finished_at=db.now_iso())
        ctx.log("Completed")
        repo.release_children(job["id"], True)
    except Cancelled:
        repo.update_job(job["id"], status="cancelled", stage="Cancelled", finished_at=db.now_iso())
        ctx.log("Cancelled")
        repo.release_children(job["id"], False)
        _restore_video_after_stop(job, "Cancelled")
    except Exception as exc:  # report every failure to the UI
        msg = str(exc) or exc.__class__.__name__
        repo.update_job(job["id"], status="failed", stage="Failed", error=msg[:2000], finished_at=db.now_iso())
        ctx.log(f"ERROR: {msg}\n{traceback.format_exc()[-3000:]}")
        log.exception("Job %s failed", job["id"])
        repo.release_children(job["id"], False)
        _restore_video_after_stop(job, msg[:2000], failed=True)


def _loop():
    while True:
        try:
            job = repo.next_queued_job()
            if job:
                _run(job)
                continue
        except Exception:
            log.exception("Worker loop error")
            time.sleep(2)
        _wake.wait(timeout=2)
        _wake.clear()


def start_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    repo.recover_interrupted_jobs()
    _worker = threading.Thread(target=_loop, name="mabix-worker", daemon=True)
    _worker.start()


def run_pending_sync(max_jobs=50):
    """Process queued jobs in the current thread (tests / CLI)."""
    for _ in range(max_jobs):
        job = repo.next_queued_job()
        if not job:
            return
        _run(job)


# ------------------------------------------------------------- handlers
def _video_or_fail(ctx):
    video = repo.get_video(ctx.job["video_id"])
    if not video:
        raise RuntimeError("Video record no longer exists")
    return video


@handler("script")
def handle_script(ctx):
    from .pipelines import studio
    video = _video_or_fail(ctx)
    cfg = video["config"] or {}
    repo.update_video(video["id"], status="scripting", error=None)
    ctx.progress(10, "Writing script with the language model…")
    if video["video_type"] == "news":
        plan = studio.generate_news_plan(video["prompt"], cfg)
    else:
        plan = studio.generate_plan(video["video_type"], video["prompt"], cfg)
    ctx.check_cancel()
    for i, img in enumerate(cfg.get("input_images") or []):
        if i < len(plan["scenes"]):
            plan["scenes"][i]["input_image"] = img
    ctx.log(f"Script ready: {len(plan['scenes'])} scenes — “{plan['title']}”")
    tags = " ".join(plan.get("hashtags", []))
    repo.update_video(video["id"], script=plan, status="review",
                      title=video["title"] or plan["title"] or video["prompt"][:80],
                      description=(plan["description"] + ("\n\n" + tags if tags else "")).strip(),
                      tags=video["tags"] or ",".join(t.lstrip("#") for t in plan.get("hashtags", [])))
    ctx.progress(100, "Script ready for review")


@handler("storyboard")
def handle_storyboard(ctx):
    from .pipelines import studio
    video = _video_or_fail(ctx)
    plan, cfg = video["script"], video["config"]
    if not plan:
        raise RuntimeError("Generate the script before the storyboard")
    workdir = storage.job_workdir(video["id"])
    only = ctx.params.get("scene")
    indices = [int(only)] if only is not None else list(range(len(plan["scenes"])))
    repo.update_video(video["id"], status="storyboarding", error=None)
    if video["video_type"] == "news" and not cfg.get("ai_only"):
        from .pipelines import legacy
        fmt = {"9:16": "short", "1:1": "instagram"}.get(cfg.get("aspect"), "youtube")
        ctx.progress(5, "Searching stock images (Pexels/Pixabay, AI fallback)…")
        sel = [{"text": plan["scenes"][i]["narration"], "keyword": plan["scenes"][i]["visual"]} for i in indices]
        paths, sources = legacy.download_images(sel, fmt, False)
        for i, p, src in zip(indices, paths, sources):
            dest = os.path.join(workdir, f"keyframe_{i}_{int(time.time())}.jpg")
            shutil.copy(p, dest)
            plan["scenes"][i].update(image=dest, image_source=src)
            plan["scenes"][i].pop("clip", None)
    else:
        for n, i in enumerate(indices):
            ctx.check_cancel()
            ctx.progress(5 + 90 * n / len(indices), f"Generating keyframe {n + 1}/{len(indices)}")
            scene = plan["scenes"][i]
            scene["image"] = studio.generate_keyframe(video["video_type"], scene, i, cfg, plan, workdir)
            # bust browser cache & drop a clip made from an older keyframe
            final = scene["image"].replace(".png", f"_{int(time.time())}.png")
            os.replace(scene["image"], final)
            scene["image"] = final
            scene.pop("clip", None)
            repo.update_video(video["id"], script=plan)
    repo.update_video(video["id"], script=plan, status="review")
    ctx.progress(100, "Storyboard ready")


@handler("render")
def handle_render(ctx):
    from .pipelines import render, studio
    video = _video_or_fail(ctx)
    plan, cfg = video["script"], video["config"]
    if not plan or not plan.get("scenes"):
        raise RuntimeError("No script to render — generate the script first")
    workdir = storage.job_workdir(video["id"])
    repo.update_video(video["id"], status="processing", error=None)
    regenerate = ctx.params.get("regenerate_visuals", False)
    scenes = plan["scenes"]
    for i, scene in enumerate(scenes):
        ctx.check_cancel()
        scene["index"] = i
        if scene.get("clip") and os.path.exists(scene["clip"]) and not regenerate:
            continue
        if regenerate:
            scene.pop("clip", None)
            if not scene.get("input_image"):
                scene.pop("image", None)
        if video["video_type"] == "news" and scene.get("image") and cfg.get("motion") == "none":
            continue
        ctx.progress(2 + 68 * i / len(scenes), f"Generating AI video for scene {i + 1}/{len(scenes)}")
        studio.generate_scene_visual(video["video_type"], scene, i, cfg, plan, workdir, ctx.log)
        ctx.log(f"Scene {i + 1}: {scene.get('visual_kind')}")
        repo.update_video(video["id"], script=plan)

    ctx.progress(70, "Narration, captions and final render…")
    opts = dict(cfg)
    opts["music_path"] = storage.music_path(cfg.get("music"))
    out = storage.path(storage.GENERATED, f"{video['id']}_{render.slug(video['title'])}_{int(time.time())}.mp4")
    render.render(scenes, opts, workdir, out, log_fn=ctx.log, cancel_check=ctx.check_cancel,
                  progress_fn=lambda f: ctx.progress(70 + 25 * f, "Composing scenes…"))
    ctx.progress(97, "Creating thumbnail…")
    info = media.describe(out)
    old_file, old_thumb = video.get("file_path"), video.get("thumbnail_path")
    repo.update_video(video["id"], file_path=out, status="completed", completed_at=db.now_iso(), error=None,
                      script=plan, **{k: info.get(k) for k in ("duration", "width", "height", "file_size",
                                                               "thumbnail_path")})
    for old in (old_file, old_thumb):
        if old and old != out:
            storage.remove_file(old)
    ctx.log(f"Rendered {info['width']}x{info['height']}, {info['duration']}s → {os.path.basename(out)}")


@handler("news")
def handle_news(ctx):
    """Quick mode: the original generate_news_short() pipeline, unchanged."""
    from .pipelines import legacy
    video = _video_or_fail(ctx)
    cfg = video["config"]
    repo.update_video(video["id"], status="processing", error=None)
    ctx.progress(10, "Running news pipeline (script → scenes → images → voice → render)…")
    result = legacy.generate_news_short(video["prompt"], cfg.get("format", "short"), bool(cfg.get("ai_only")))
    path = os.path.abspath(result["video"])
    ctx.log(f"Images used: {result.get('images')}")
    ctx.progress(95, "Creating thumbnail…")
    info = media.describe(path)
    cfg["images_used"] = result.get("images")
    repo.update_video(video["id"], title=result["title"], description=result["description"], file_path=path,
                      status="completed", completed_at=db.now_iso(), config=cfg,
                      **{k: info.get(k) for k in ("duration", "width", "height", "file_size", "thumbnail_path")})


@handler("publish")
def handle_publish(ctx):
    from .publishing import youtube
    video = _video_or_fail(ctx)
    path = storage.resolve_safe(video.get("file_path"))
    if not path or not os.path.exists(path):
        raise RuntimeError("Video file not found")
    p = ctx.params
    repo.update_video(video["id"], publish_status="publishing")
    ctx.progress(5, "Uploading to YouTube…")
    tags = [t.strip() for t in (p.get("tags") or video.get("tags") or "").split(",") if t.strip()]
    res = youtube.upload(path, p.get("title") or video["title"], p.get("description") or video["description"],
                         tags=tags or None, privacy=p.get("privacy", "public"), shorts=p.get("shorts", True),
                         progress_fn=lambda f: ctx.progress(5 + 90 * f, "Uploading to YouTube…"))
    repo.update_video(video["id"], publish_status="published", publish_url=res["url"])
    ctx.log(f"Published: {res['url']}")
