"""SQLAlchemy database (SQLite by default, PostgreSQL via DATABASE_URL)."""
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


_engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def configure(url=None):
    global _engine
    url = url or settings.DATABASE_URL or f"sqlite:///{os.path.join(settings.DATA_DIR, 'mabcat.db')}"
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    SessionLocal.configure(bind=_engine)
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(_engine)
    _add_missing_columns()
    return _engine


def _add_missing_columns():
    """Lightweight forward migration: add nullable columns introduced after a database was created."""
    from sqlalchemy import inspect, text
    insp = inspect(_engine)
    with _engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and col.nullable:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(_engine.dialect)}'))


def engine():
    return _engine


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
