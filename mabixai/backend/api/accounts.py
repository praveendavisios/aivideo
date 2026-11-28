"""Auth, users, students (parent-owned), classes (teacher-owned) and devices."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import ClassGroup, ClassMember, Device, Student, User
from ..security import (ROLES, audit, authenticate, current_user, get_student_for, hash_password, issue_token,
                        new_device_token, require, revoke_token, sha256, visible_student_ids)
from ..teaching import student_engine

router = APIRouter(prefix="/api/v1")
_attempts = {}


class LoginIn(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    email: str
    name: str = ""
    role: str
    password: str = Field(min_length=6)


class StudentIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    grade: str = ""
    age: int | None = Field(default=None, ge=3, le=18)
    preferred_language: str = "en"
    subjects: list[str] = []
    persona: str = "friendly"
    daily_limit_minutes: int = Field(default=60, ge=0, le=600)


class ConsentIn(BaseModel):
    learning: bool
    audio: bool


class DeviceIn(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{3,64}$")
    name: str = "Mab Cat"
    student_id: str | None = None


def user_out(u):
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role}


def student_out(s):
    return {"id": s.id, "display_name": s.display_name, "grade": s.grade, "age": s.age, "level": s.level,
            "preferred_language": s.preferred_language, "subjects": s.subjects, "persona": s.persona,
            "consent_learning": s.consent_learning, "consent_audio": s.consent_audio,
            "daily_limit_minutes": s.daily_limit_minutes}


# ------------------------------------------------------------------ auth
@router.post("/auth/login", tags=["auth"])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    import time
    ip = request.client.host if request.client else "?"
    recent = [t for t in _attempts.get(ip, []) if time.time() - t < 300]
    if len(recent) >= 10:
        raise HTTPException(429, "Too many attempts — try again in a few minutes")
    u = authenticate(db, body.email, body.password)
    if not u:
        _attempts[ip] = recent + [time.time()]
        audit(db, "anonymous", "auth.failed", body.email.lower()[:120])
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    token = issue_token(db, u)
    audit(db, u.id, "auth.login")
    db.commit()
    return {"token": token, "user": user_out(u)}


@router.post("/auth/logout", tags=["auth"])
def logout(authorization: str = Header(default=""), db: Session = Depends(get_db), user=Depends(current_user)):
    revoke_token(db, authorization[7:])
    return {"ok": True}


@router.get("/auth/me", tags=["auth"])
def me(user=Depends(current_user)):
    return user_out(user)


@router.post("/users", tags=["auth"])
def create_user(body: UserIn, db: Session = Depends(get_db), admin=Depends(require("admin"))):
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    u = User(email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    audit(db, admin.id, "user.create", u.email, role=body.role)
    db.commit()
    return user_out(u)


@router.get("/users", tags=["auth"])
def list_users(db: Session = Depends(get_db), admin=Depends(require("admin"))):
    return [user_out(u) for u in db.scalars(select(User))]


# --------------------------------------------------------------- students
@router.get("/students", tags=["students"])
def list_students(db: Session = Depends(get_db), user=Depends(current_user)):
    ids = visible_student_ids(db, user)
    q = select(Student) if ids is None else select(Student).where(Student.id.in_(ids or {""}))
    return [student_out(s) for s in db.scalars(q)]


@router.post("/students", tags=["students"])
def create_student(body: StudentIn, db: Session = Depends(get_db), user=Depends(require("parent"))):
    s = Student(parent_id=user.id, **body.model_dump())
    db.add(s)
    audit(db, user.id, "student.create", s.id)
    db.commit()
    return student_out(s)


@router.patch("/students/{student_id}", tags=["students"])
def update_student(student_id: str, body: StudentIn, db: Session = Depends(get_db), user=Depends(current_user)):
    s = get_student_for(db, user, student_id, write=True)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, user.id, "student.update", s.id)
    db.commit()
    return student_out(s)


@router.put("/students/{student_id}/consent", tags=["students"])
def set_consent(student_id: str, body: ConsentIn, db: Session = Depends(get_db), user=Depends(current_user)):
    s = get_student_for(db, user, student_id, write=True)
    if user.role not in ("parent", "admin"):
        raise HTTPException(403, "Only the parent can give consent")
    s.consent_learning, s.consent_audio, s.consent_at = body.learning, body.audio and body.learning, utcnow()
    audit(db, user.id, "student.consent", s.id, learning=body.learning, audio=s.consent_audio)
    db.commit()
    return student_out(s)


@router.delete("/students/{student_id}", tags=["students"])
def delete_student(student_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """Deletes the profile and all learning data (right to erasure)."""
    s = get_student_for(db, user, student_id, write=True)
    db.delete(s)
    audit(db, user.id, "student.delete", student_id)
    db.commit()
    return {"ok": True}


@router.get("/students/{student_id}/progress", tags=["students"])
def student_progress(student_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    s = get_student_for(db, user, student_id)
    return student_engine.summary(db, s) | {"revision": student_engine.revision_list(db, s),
                                            "minutes_today": round(student_engine.minutes_today(db, s), 1)}


# ---------------------------------------------------------------- classes
@router.post("/classes", tags=["classes"])
def create_class(name: str, db: Session = Depends(get_db), user=Depends(require("teacher"))):
    c = ClassGroup(teacher_id=user.id, name=name[:100])
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name, "join_code": c.join_code}


@router.get("/classes", tags=["classes"])
def list_classes(db: Session = Depends(get_db), user=Depends(require("teacher"))):
    out = []
    for c in db.scalars(select(ClassGroup).where(ClassGroup.teacher_id == user.id) if user.role != "admin" else select(ClassGroup)):
        members = db.scalars(select(Student).join(ClassMember, ClassMember.student_id == Student.id)
                             .where(ClassMember.class_id == c.id)).all()
        out.append({"id": c.id, "name": c.name, "join_code": c.join_code, "students": [student_out(s) for s in members]})
    return out


@router.post("/classes/join", tags=["classes"])
def join_class(join_code: str, student_id: str, db: Session = Depends(get_db), user=Depends(require("parent"))):
    """A parent enrols their child in a teacher's class (shares progress with that teacher)."""
    s = get_student_for(db, user, student_id, write=True)
    c = db.scalar(select(ClassGroup).where(ClassGroup.join_code == join_code.strip().upper()))
    if not c:
        raise HTTPException(404, "Class code not found")
    if not db.scalar(select(ClassMember).where(ClassMember.class_id == c.id, ClassMember.student_id == s.id)):
        db.add(ClassMember(class_id=c.id, student_id=s.id))
    audit(db, user.id, "class.join", c.id, student=s.id)
    db.commit()
    return {"ok": True, "class": c.name}


# ---------------------------------------------------------------- devices
def device_out(d):
    return {"id": d.id, "name": d.name, "student_id": d.student_id, "enabled": d.enabled,
            "last_seen": d.last_seen, "firmware": d.firmware}


@router.post("/devices", tags=["devices"])
def register_device(body: DeviceIn, db: Session = Depends(get_db), user=Depends(require("parent"))):
    """Register a RoboCat. The device token is returned ONCE — store it in the robot's secure storage."""
    if db.get(Device, body.device_id):
        raise HTTPException(409, "Device id already registered")
    if body.student_id:
        get_student_for(db, user, body.student_id, write=True)
    token = new_device_token()
    d = Device(id=body.device_id, owner_id=user.id, name=body.name, student_id=body.student_id, token_hash=sha256(token))
    db.add(d)
    audit(db, user.id, "device.register", d.id)
    db.commit()
    return device_out(d) | {"device_token": token}


@router.get("/devices", tags=["devices"])
def list_devices(db: Session = Depends(get_db), user=Depends(current_user)):
    q = select(Device) if user.role == "admin" else select(Device).where(Device.owner_id == user.id)
    return [device_out(d) for d in db.scalars(q)]


def _own_device(db, user, device_id):
    d = db.get(Device, device_id)
    if not d or (d.owner_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Device not found")
    return d


@router.patch("/devices/{device_id}", tags=["devices"])
def update_device(device_id: str, student_id: str | None = None, enabled: bool | None = None, name: str | None = None,
                  db: Session = Depends(get_db), user=Depends(current_user)):
    d = _own_device(db, user, device_id)
    if student_id is not None:
        if student_id:
            get_student_for(db, user, student_id, write=True)
        d.student_id = student_id or None
    if enabled is not None:
        d.enabled = enabled
    if name:
        d.name = name[:100]
    audit(db, user.id, "device.update", d.id, enabled=d.enabled, student=d.student_id)
    db.commit()
    return device_out(d)


@router.post("/devices/{device_id}/rotate-token", tags=["devices"])
def rotate_device_token(device_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    d = _own_device(db, user, device_id)
    token = new_device_token()
    d.token_hash = sha256(token)
    audit(db, user.id, "device.rotate_token", d.id)
    db.commit()
    return {"device_token": token}


@router.delete("/devices/{device_id}", tags=["devices"])
def delete_device(device_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    d = _own_device(db, user, device_id)
    db.delete(d)
    audit(db, user.id, "device.delete", device_id)
    db.commit()
    return {"ok": True}
