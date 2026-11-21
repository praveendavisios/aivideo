"""Data access for videos, jobs and settings. Keeps SQL out of routes."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import db

VIDEO_TYPES = ["short", "animation", "story", "answer", "news", "upload"]
AI_TYPES = ["short", "animation", "story", "answer", "news"]
JOB_ACTIVE = ("pending", "queued", "processing")
JOB_STATUSES = ["pending", "queued", "processing", "completed", "failed", "cancelled"]

VIDEO_JSON = ("config", "script")
JOB_JSON = ("params",)


# ----------------------------------------------------------------- videos
def create_video(video_type, source_type, title="", description="", prompt="",
                 config=None, status="draft", **extra):
    ts = db.now_iso()
    cols = {
        "title": title, "description": description, "video_type": video_type,
        "source_type": source_type, "status": status, "prompt": prompt,
        "config": json.dumps(config or {}), "created_at": ts, "updated_at": ts,
    }
    cols.update(extra)
    keys = ",".join(cols)
    marks = ",".join("?" * len(cols))
    return db.execute(f"INSERT INTO videos ({keys}) VALUES ({marks})", tuple(cols.values()))


def get_video(video_id):
    return db.row_to_dict(db.query("SELECT * FROM videos WHERE id=?", (video_id,), one=True), VIDEO_JSON)


UPDATABLE_VIDEO = {
    "title", "description", "status", "prompt", "config", "script", "file_path",
    "thumbnail_path", "duration", "width", "height", "file_size", "tags",
    "category", "publish_status", "publish_url", "error", "completed_at",
}


def update_video(video_id, **fields):
    fields = {k: v for k, v in fields.items() if k in UPDATABLE_VIDEO}
    for k in VIDEO_JSON:
        if k in fields and not isinstance(fields[k], (str, type(None))):
            fields[k] = json.dumps(fields[k])
    fields["updated_at"] = db.now_iso()
    sets = ",".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE videos SET {sets} WHERE id=?", (*fields.values(), video_id))


def delete_video(video_id):
    db.execute("DELETE FROM videos WHERE id=?", (video_id,))


LIBRARY_FILTERS = {
    "all": ("1=1", ()),
    "ai": ("source_type='ai'", ()),
    "uploaded": ("source_type='upload'", ()),
    "news": ("video_type='news'", ()),
    "short": ("video_type='short'", ()),
    "animation": ("video_type='animation'", ()),
    "story": ("video_type='story'", ()),
    "educational": ("video_type='answer'", ()),
    "completed": ("status='completed'", ()),
    "processing": ("status IN ('scripting','storyboarding','queued','processing')", ()),
    "failed": ("status='failed'", ()),
    "published": ("publish_status='published'", ()),
    "review": ("status='review'", ()),
}
SORTS = {
    "newest": "created_at DESC", "oldest": "created_at ASC",
    "title": "title COLLATE NOCASE ASC", "duration": "COALESCE(duration,0) DESC",
    "updated": "updated_at DESC",
}


def list_videos(filter_name="all", search="", sort="newest", page=1, per_page=12, video_type=None):
    where, args = LIBRARY_FILTERS.get(filter_name, LIBRARY_FILTERS["all"])
    clauses, params = [where], list(args)
    if video_type:
        clauses.append("video_type=?")
        params.append(video_type)
    if search:
        clauses.append("(title LIKE ? OR description LIKE ? OR prompt LIKE ? OR tags LIKE ?)")
        params += [f"%{search}%"] * 4
    sql_where = " AND ".join(clauses)
    total = db.query(f"SELECT COUNT(*) c FROM videos WHERE {sql_where}", params, one=True)["c"]
    page = max(1, int(page))
    per_page = min(max(1, int(per_page)), 100)
    rows = db.query(
        f"SELECT * FROM videos WHERE {sql_where} ORDER BY {SORTS.get(sort, SORTS['newest'])} LIMIT ? OFFSET ?",
        (*params, per_page, (page - 1) * per_page),
    )
    return {
        "items": [db.row_to_dict(r, VIDEO_JSON) for r in rows],
        "total": total, "page": page, "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


# ------------------------------------------------------------------- jobs
def dedupe_key(kind, video_id, params):
    raw = json.dumps({"k": kind, "v": video_id, "p": params}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def find_active_duplicate(key):
    row = db.query(
        f"SELECT * FROM jobs WHERE dedupe_key=? AND status IN {JOB_ACTIVE} ORDER BY id DESC LIMIT 1",
        (key,), one=True,
    )
    return db.row_to_dict(row, JOB_JSON)


def create_job(kind, video_id=None, params=None, summary="", status="queued",
               parent_job_id=None, retry_of=None):
    params = params or {}
    key = dedupe_key(kind, video_id, params)
    ts = db.now_iso()
    return db.execute(
        "INSERT INTO jobs (video_id,kind,status,params,summary,dedupe_key,parent_job_id,retry_of,created_at,log)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (video_id, kind, status, json.dumps(params), summary[:500], key, parent_job_id, retry_of, ts,
         f"[{ts}] Job created ({status})\n"),
    )


def get_job(job_id):
    return db.row_to_dict(db.query("SELECT * FROM jobs WHERE id=?", (job_id,), one=True), JOB_JSON)


def update_job(job_id, **fields):
    allowed = {"status", "progress", "stage", "error", "started_at", "finished_at", "cancel_requested", "params"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "params" in fields and not isinstance(fields["params"], str):
        fields["params"] = json.dumps(fields["params"])
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))


def append_job_log(job_id, message):
    line = f"[{db.now_iso()}] {message}\n"
    db.execute("UPDATE jobs SET log = substr(log || ?, -60000) WHERE id=?", (line, job_id))


def next_queued_job():
    return db.row_to_dict(
        db.query("SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1", one=True), JOB_JSON
    )


def release_children(parent_job_id, parent_ok):
    """Pending jobs waiting on a parent become queued (or cancelled if it failed)."""
    if parent_ok:
        db.execute("UPDATE jobs SET status='queued' WHERE parent_job_id=? AND status='pending'", (parent_job_id,))
    else:
        db.execute(
            "UPDATE jobs SET status='cancelled', error='Previous step did not complete', finished_at=? "
            "WHERE parent_job_id=? AND status='pending'", (db.now_iso(), parent_job_id),
        )


def list_jobs(status=None, kind=None, search="", page=1, per_page=20, video_id=None, active_only=False):
    clauses, params = ["1=1"], []
    if active_only:
        clauses.append(f"j.status IN {JOB_ACTIVE}")
    if status:
        clauses.append("j.status=?")
        params.append(status)
    if kind:
        clauses.append("j.kind=?")
        params.append(kind)
    if video_id:
        clauses.append("j.video_id=?")
        params.append(video_id)
    if search:
        clauses.append("(j.summary LIKE ? OR v.title LIKE ?)")
        params += [f"%{search}%"] * 2
    w = " AND ".join(clauses)
    base = f"FROM jobs j LEFT JOIN videos v ON v.id=j.video_id WHERE {w}"
    total = db.query(f"SELECT COUNT(*) c {base}", params, one=True)["c"]
    page = max(1, int(page))
    per_page = min(max(1, int(per_page)), 100)
    rows = db.query(
        f"SELECT j.id,j.video_id,j.kind,j.status,j.progress,j.stage,j.summary,j.error,j.created_at,"
        f"j.started_at,j.finished_at,j.retry_of,j.cancel_requested,v.title AS video_title,v.video_type "
        f"{base} ORDER BY j.id DESC LIMIT ? OFFSET ?",
        (*params, per_page, (page - 1) * per_page),
    )
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page)}


def recover_interrupted_jobs():
    """Jobs left 'processing' by a crash/restart cannot be resumed mid-render."""
    ts = db.now_iso()
    rows = db.query("SELECT id, video_id FROM jobs WHERE status='processing'")
    for r in rows:
        db.execute("UPDATE jobs SET status='failed', error='Interrupted by server restart', finished_at=? WHERE id=?",
                   (ts, r["id"]))
        if r["video_id"]:
            db.execute("UPDATE videos SET status='failed', error='Interrupted by server restart' "
                       "WHERE id=? AND status IN ('scripting','storyboarding','processing')", (r["video_id"],))
        release_children(r["id"], False)


# --------------------------------------------------------------- settings
def get_setting(key, default=None):
    row = db.query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return row["value"]


def set_setting(key, value):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))


# ------------------------------------------------------------------ stats
def dashboard_stats():
    q = lambda sql, a=(): db.query(sql, a, one=True)["c"]  # noqa: E731
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "total": q("SELECT COUNT(*) c FROM videos"),
        "ai_generated": q("SELECT COUNT(*) c FROM videos WHERE source_type='ai'"),
        "uploaded": q("SELECT COUNT(*) c FROM videos WHERE source_type='upload'"),
        "today": q("SELECT COUNT(*) c FROM videos WHERE source_type='ai' AND status='completed' "
                   "AND substr(completed_at,1,10)=?", (today,)),
        "processing": q("SELECT COUNT(*) c FROM videos WHERE status IN ('scripting','storyboarding','queued','processing')"),
        "completed": q("SELECT COUNT(*) c FROM videos WHERE status='completed'"),
        "failed": q("SELECT COUNT(*) c FROM videos WHERE status='failed'"),
        "published": q("SELECT COUNT(*) c FROM videos WHERE publish_status='published'"),
    }


def job_status_counts():
    rows = db.query("SELECT status, COUNT(*) c FROM jobs GROUP BY status")
    counts = {s: 0 for s in JOB_STATUSES}
    counts.update({r["status"]: r["c"] for r in rows})
    return counts


def daily_counts(days=14):
    start = (datetime.now(timezone.utc).date() - timedelta(days=days - 1))
    rows = db.query(
        "SELECT substr(created_at,1,10) d, COUNT(*) c FROM videos WHERE substr(created_at,1,10)>=? GROUP BY d",
        (start.isoformat(),),
    )
    by_day = {r["d"]: r["c"] for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        out.append({"date": d, "count": by_day.get(d, 0)})
    return out


def type_counts():
    rows = db.query("SELECT video_type, COUNT(*) c FROM videos GROUP BY video_type")
    counts = {t: 0 for t in VIDEO_TYPES}
    counts.update({r["video_type"]: r["c"] for r in rows})
    return counts
