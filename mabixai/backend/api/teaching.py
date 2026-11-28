"""Teaching session history, analytics and audit (REST)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (Attempt, AuditLog, Center, ClassGroup, ClassMember, Curriculum, Device, Student, TeachingSession,
                      Turn, UsageCost, User)
from ..security import current_user, get_student_for, require
from ..teaching import student_engine

router = APIRouter(prefix="/api/v1")


@router.get("/students/{student_id}/sessions", tags=["teaching"])
def sessions(student_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    st = get_student_for(db, user, student_id)
    rows = db.scalars(select(TeachingSession).where(TeachingSession.student_id == st.id)
                      .order_by(TeachingSession.started_at.desc()).limit(50)).all()
    return [{"id": s.id, "started_at": s.started_at, "ended_at": s.ended_at, "status": s.status, "node_id": s.node_id,
             "minutes": round(s.active_seconds / 60, 1)} for s in rows]


@router.get("/sessions/{session_id}/transcript", tags=["teaching"])
def transcript(session_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    s = db.get(TeachingSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    get_student_for(db, user, s.student_id)
    turns = db.scalars(select(Turn).where(Turn.session_id == s.id).order_by(Turn.id)).all()
    return {"session_id": s.id, "turns": [{"role": t.role, "text": t.text, "language": t.language, "meta": t.meta,
                                          "at": t.created_at} for t in turns]}


@router.get("/classes/{class_id}/analytics", tags=["analytics"])
def class_analytics(class_id: str, db: Session = Depends(get_db), user=Depends(require("teacher"))):
    c = db.get(ClassGroup, class_id)
    if not c or (c.teacher_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Class not found")
    students = db.scalars(select(Student).join(ClassMember, ClassMember.student_id == Student.id)
                          .where(ClassMember.class_id == c.id)).all()
    rows = [student_engine.summary(db, s) | {"name": s.display_name} for s in students]
    acc = [r["accuracy"] for r in rows if r["accuracy"] is not None]
    weak = {}
    for r in rows:
        for t in r["needs_practice"]:
            weak[t] = weak.get(t, 0) + 1
    return {"class": c.name, "students": rows, "average_accuracy": round(sum(acc) / len(acc), 2) if acc else None,
            "topics_needing_practice": sorted(weak.items(), key=lambda x: -x[1])[:10]}


@router.get("/analytics/overview", tags=["analytics"])
def overview(db: Session = Depends(get_db), user=Depends(require("admin"))):
    count = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w)) or 0  # noqa: E731
    return {"centers": count(Center), "teachers": count(User, User.role == "teacher"),
            "parents": count(User, User.role == "parent"), "curricula": count(Curriculum), "devices": count(Device),
            "students": db.scalar(select(func.count(Student.id))),
            "sessions": db.scalar(select(func.count(TeachingSession.id))),
            "answers": db.scalar(select(func.count(Attempt.id))),
            "correct": db.scalar(select(func.count(Attempt.id)).where(Attempt.correct)),
            "cloud_cost_usd": round(db.scalar(select(func.sum(UsageCost.usd))) or 0, 4),
            "cloud_audio_minutes": round((db.scalar(select(func.sum(UsageCost.units)).where(UsageCost.kind == "stt")) or 0) / 60, 2)}


@router.get("/audit", tags=["analytics"])
def audit_log(limit: int = 100, db: Session = Depends(get_db), user=Depends(require("admin"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()
    return [{"at": r.created_at, "actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail} for r in rows]
