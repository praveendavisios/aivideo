"""Student learning engine: mastery tracking, level adaptation, recommendations."""
from sqlalchemy import func, select

from ..db import utcnow
from ..models import Assignment, Attempt, CurriculumNode, Progress, Student, TeachingSession
from ..curriculum import service

MASTERY_DONE = 0.75
ALPHA = 0.4  # weight of the newest answer in the mastery moving average


def record_attempt(db, session, student, node_id, question, answer, expected, score, correct, evaluator):
    db.add(Attempt(session_id=session.id, student_id=student.id, node_id=node_id, question=question,
                   answer=answer[:2000], expected=expected[:2000], score=score, correct=correct, evaluator=evaluator))
    if not node_id:
        return None
    p = db.scalar(select(Progress).where(Progress.student_id == student.id, Progress.node_id == node_id))
    if not p:
        p = Progress(student_id=student.id, node_id=node_id, mastery=0.0, attempts=0)
        db.add(p)
    p.attempts += 1
    p.mastery = round(score if p.attempts == 1 else (1 - ALPHA) * p.mastery + ALPHA * score, 3)
    p.completed = p.mastery >= MASTERY_DONE and p.attempts >= 2
    _update_level(db, student)
    return p


def _update_level(db, student):
    avg = db.scalar(select(func.avg(Progress.mastery)).where(Progress.student_id == student.id)) or 0
    n = db.scalar(select(func.count(Progress.id)).where(Progress.student_id == student.id)) or 0
    student.level = "advanced" if n >= 3 and avg >= 0.85 else "intermediate" if n >= 2 and avg >= 0.6 else "beginner"


def difficulty(student, node_progress=None):
    """1 (gentlest) … 3 (challenge) — used to pick questions and wording."""
    base = {"beginner": 1, "intermediate": 2, "advanced": 3}.get(student.level, 1)
    if node_progress is not None and node_progress.mastery < 0.4 and node_progress.attempts >= 2:
        base = 1
    return base


def curricula_for(db, student):
    return [a.curriculum_id for a in db.scalars(select(Assignment).where(Assignment.student_id == student.id))]


def summary(db, student):
    rows = db.execute(select(Progress, CurriculumNode.title, CurriculumNode.curriculum_id)
                      .join(CurriculumNode, CurriculumNode.id == Progress.node_id)
                      .where(Progress.student_id == student.id)).all()
    items = [{"node_id": p.node_id, "title": t, "curriculum_id": c, "mastery": p.mastery, "attempts": p.attempts,
              "completed": p.completed} for p, t, c in rows]
    strengths = [i["title"] for i in sorted(items, key=lambda x: -x["mastery"]) if i["mastery"] >= 0.75][:5]
    weak = [i["title"] for i in sorted(items, key=lambda x: x["mastery"]) if i["mastery"] < 0.6 and i["attempts"]][:5]
    attempts = db.scalar(select(func.count(Attempt.id)).where(Attempt.student_id == student.id)) or 0
    correct = db.scalar(select(func.count(Attempt.id)).where(Attempt.student_id == student.id, Attempt.correct)) or 0
    seconds = db.scalar(select(func.sum(TeachingSession.active_seconds)).where(TeachingSession.student_id == student.id)) or 0
    return {"student_id": student.id, "level": student.level, "topics": items, "strengths": strengths,
            "needs_practice": weak, "answers": attempts, "correct": correct,
            "accuracy": round(correct / attempts, 2) if attempts else None, "learning_minutes": round(seconds / 60, 1),
            "completed_lessons": sum(1 for i in items if i["completed"]), "next": next_node(db, student)}


def next_node(db, student):
    """First assigned teachable node that is not yet mastered, in curriculum order."""
    done = {p.node_id: p for p in db.scalars(select(Progress).where(Progress.student_id == student.id))}
    for a in db.scalars(select(Assignment).where(Assignment.student_id == student.id).order_by(Assignment.id)):
        nodes = service.leaf_nodes_in_order(db, a.curriculum_id)
        if a.node_id:
            nodes = [n for n in nodes if n.id == a.node_id or n.parent_id == a.node_id] or nodes
        for n in nodes:
            p = done.get(n.id)
            if not p or not p.completed:
                return {"node_id": n.id, "title": n.title, "curriculum_id": a.curriculum_id}
    return None


def revision_list(db, student, limit=5):
    rows = db.execute(select(Progress, CurriculumNode.title).join(CurriculumNode, CurriculumNode.id == Progress.node_id)
                      .where(Progress.student_id == student.id, Progress.mastery < 0.6)
                      .order_by(Progress.mastery)).all()[:limit]
    return [{"node_id": p.node_id, "title": t, "mastery": p.mastery} for p, t in rows]


def minutes_today(db, student):
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    secs = db.scalar(select(func.sum(TeachingSession.active_seconds))
                     .where(TeachingSession.student_id == student.id, TeachingSession.started_at >= start)) or 0
    return secs / 60


def over_limit(db, student):
    return bool(student.daily_limit_minutes) and minutes_today(db, student) >= student.daily_limit_minutes
