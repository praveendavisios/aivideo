"""SQLite persistence (stdlib only) with versioned migrations."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import Config

_local = threading.local()
_db_path = Config.DB_PATH

MIGRATIONS = [
    # v1 — initial schema
    """
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        video_type TEXT NOT NULL,            -- short|animation|story|answer|news|upload
        source_type TEXT NOT NULL,           -- ai|upload
        status TEXT NOT NULL DEFAULT 'draft',-- draft|scripting|review|storyboarding|queued|processing|completed|failed
        prompt TEXT NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',   -- JSON generation settings
        script TEXT,                         -- JSON plan: scenes, narration, prompts
        file_path TEXT,
        thumbnail_path TEXT,
        duration REAL,
        width INTEGER,
        height INTEGER,
        file_size INTEGER,
        tags TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        publish_status TEXT NOT NULL DEFAULT 'unpublished', -- unpublished|publishing|published|failed
        publish_url TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE INDEX idx_videos_type ON videos(video_type);
    CREATE INDEX idx_videos_status ON videos(status);
    CREATE INDEX idx_videos_created ON videos(created_at);

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
        kind TEXT NOT NULL,                  -- script|storyboard|render|news|publish
        status TEXT NOT NULL,                -- pending|queued|processing|completed|failed|cancelled
        progress INTEGER NOT NULL DEFAULT 0,
        stage TEXT NOT NULL DEFAULT '',
        params TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '',    -- prompt/topic shown in history
        dedupe_key TEXT,
        parent_job_id INTEGER,
        retry_of INTEGER,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        log TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs(status);
    CREATE INDEX idx_jobs_video ON jobs(video_id);

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def set_db_path(path):
    global _db_path
    _db_path = path
    close()


def get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != _db_path:
        conn = sqlite3.connect(_db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.path = _db_path
    return conn


def close():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def migrate():
    conn = get_conn()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version={idx}")
        conn.commit()


def query(sql, args=(), one=False):
    cur = get_conn().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    conn = get_conn()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def row_to_dict(row, json_fields=()):
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except (TypeError, ValueError):
                pass
    return d
