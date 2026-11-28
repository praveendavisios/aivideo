"""Authentication, role-based access, device auth, student isolation and audit logging."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from .config import settings
from .db import get_db, utcnow
from .models import ApiToken, AuditLog, ClassGroup, ClassMember, Device, Student, User

log = logging.getLogger("mabcat.security")
ROLES = ("admin", "teacher", "parent")


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw):
    if len(pw) < 8 and settings.IS_PRODUCTION:
        raise ValueError("Password must be at least 8 characters")
    return generate_password_hash(pw)


def audit(db, actor, action, target="", **detail):
    db.add(AuditLog(actor=str(actor), action=action, target=str(target), detail=detail))


def bootstrap_admin(db):
    if db.scalar(select(User).where(User.role == "admin")):
        return
    if not settings.ADMIN_PASSWORD:
        log.warning("No admin user and MABCAT_ADMIN_PASSWORD unset — create one before use")
        return
    db.add(User(email=settings.ADMIN_EMAIL, name="Administrator", role="admin",
                password_hash=generate_password_hash(settings.ADMIN_PASSWORD)))
    db.commit()


def authenticate(db, email, password):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not user.active or not check_password_hash(user.password_hash, password):
        return None
    return user


def issue_token(db, user):
    raw = secrets.token_urlsafe(32)
    db.add(ApiToken(token_hash=sha256(raw), user_id=user.id, expires_at=utcnow() + timedelta(hours=settings.TOKEN_HOURS)))
    db.commit()
    return raw


def revoke_token(db, raw):
    t = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha256(raw)))
    if t:
        db.delete(t)
        db.commit()


def _bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def user_from_token(db, raw):
    if not raw:
        return None
    t = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha256(raw)))
    if not t or t.expires_at < utcnow():
        return None
    u = db.get(User, t.user_id)
    return u if u and u.active else None


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    user = user_from_token(db, _bearer(authorization))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require(*roles):
    def dep(user: User = Depends(current_user)):
        if user.role not in roles and user.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {', '.join(roles)}")
        return user
    return dep


def visible_student_ids(db, user):
    if user.role == "admin":
        return None  # all
    if user.role == "parent":
        return set(db.scalars(select(Student.id).where(Student.parent_id == user.id)))
    # teacher: students in their classes, plus students of their center
    q = select(ClassMember.student_id).join(ClassGroup, ClassGroup.id == ClassMember.class_id) \
        .where(ClassGroup.teacher_id == user.id)
    ids = set(db.scalars(q))
    if user.center_id:
        ids |= set(db.scalars(select(Student.id).where(Student.center_id == user.center_id)))
    return ids


def get_student_for(db, user, student_id, write=False):
    """Fetch a student enforcing isolation. Teachers may read, only parents/admin may write."""
    st = db.get(Student, student_id)
    if not st:
        raise HTTPException(404, "Student not found")
    if user.role == "admin" or st.parent_id == user.id:
        return st
    if not write and user.role == "teacher" and student_id in visible_student_ids(db, user):
        return st
    raise HTTPException(404, "Student not found")


def new_device_token():
    return "mcd_" + secrets.token_urlsafe(32)


def authenticate_device(db, device_id, raw_token):
    d = db.get(Device, device_id)
    if not d or not d.enabled or not raw_token:
        return None
    if not hmac.compare_digest(d.token_hash, sha256(raw_token)):
        return None
    return d
