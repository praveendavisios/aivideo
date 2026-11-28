"""Database schema.

Curriculum tree:  Curriculum → CurriculumNode (subject → grade → chapter → topic → subtopic)
                  + Document → Chunk (RAG, with page/source references)
Teaching:         LessonPlan (per node), Question, TeachingSession, Turn
Learning:         Student, Progress (mastery per node), Attempt
Platform:         User (admin/teacher/parent), Device, ClassGroup, Assignment, AuditLog, ApiToken, UsageCost
"""
import secrets

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


def uid(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


class TimestampMixin:
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("usr"))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20))  # admin | teacher | parent
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[object] = mapped_column(DateTime)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("stu"))
    parent_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(100))  # first name / nickname only (data minimisation)
    grade: Mapped[str] = mapped_column(String(20), default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="en")
    subjects: Mapped[list] = mapped_column(JSON, default=list)
    persona: Mapped[str] = mapped_column(String(30), default="friendly")
    consent_learning: Mapped[bool] = mapped_column(Boolean, default=False)  # parent consent to AI tutoring
    consent_audio: Mapped[bool] = mapped_column(Boolean, default=False)     # parent consent to voice processing
    consent_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    daily_limit_minutes: Mapped[int] = mapped_column(Integer, default=60)
    level: Mapped[str] = mapped_column(String(20), default="beginner")


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "classes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("cls"))
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    join_code: Mapped[str] = mapped_column(String(12), unique=True, default=lambda: secrets.token_hex(4).upper())


class ClassMember(Base):
    __tablename__ = "class_members"
    __table_args__ = (UniqueConstraint("class_id", "student_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. mabcat-001
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="Mab Cat")
    token_hash: Mapped[str] = mapped_column(String(64))
    student_id: Mapped[str | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    firmware: Mapped[str] = mapped_column(String(40), default="")


class Curriculum(TimestampMixin, Base):
    __tablename__ = "curricula"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("cur"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100), default="")
    grade: Mapped[str] = mapped_column(String(20), default="")
    language: Mapped[str] = mapped_column(String(10), default="en")
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents = relationship("Document", cascade="all, delete-orphan", back_populates="curriculum")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("doc"))
    curriculum_id: Mapped[str] = mapped_column(ForeignKey("curricula.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    method: Mapped[str] = mapped_column(String(40), default="")  # text | ocr | mixed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    curriculum = relationship("Curriculum", back_populates="documents")


class CurriculumNode(TimestampMixin, Base):
    __tablename__ = "curriculum_nodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("nod"))
    curriculum_id: Mapped[str] = mapped_column(ForeignKey("curricula.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="CASCADE"), nullable=True)
    level: Mapped[str] = mapped_column(String(20))  # subject|grade|chapter|topic|subtopic
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, default=0)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    definitions: Mapped[list] = mapped_column(JSON, default=list)    # [{term, meaning, source}]
    examples: Mapped[list] = mapped_column(JSON, default=list)
    activities: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)      # [{question, answer, source}]
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)  # node ids or free text
    sources: Mapped[list] = mapped_column(JSON, default=list)        # [{document_id, filename, page}]


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    curriculum_id: Mapped[str] = mapped_column(ForeignKey("curricula.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="SET NULL"), nullable=True)
    page: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)


class LessonPlan(TimestampMixin, Base):
    __tablename__ = "lesson_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("pln"))
    node_id: Mapped[str] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="CASCADE"), index=True)
    persona: Mapped[str] = mapped_column(String(30), default="friendly")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    generator: Mapped[str] = mapped_column(String(40), default="")    # llm:<model> | offline
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    edited_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Material(TimestampMixin, Base):
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("mat"))
    node_id: Mapped[str] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # illustration|diagram|flashcards|worksheet|quiz|video|audio
    status: Mapped[str] = mapped_column(String(20), default="pending")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    studio_video_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    curriculum_id: Mapped[str] = mapped_column(ForeignKey("curricula.id", ondelete="CASCADE"))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="CASCADE"), nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(32))


class TeachingSession(TimestampMixin, Base):
    __tablename__ = "teaching_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uid("ses"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    curriculum_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | ended
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("teaching_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # student | teacher
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # stt provider, sources, emotion…
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("teaching_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluator: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Progress(TimestampMixin, Base):
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("student_id", "node_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("curriculum_nodes.id", ondelete="CASCADE"))
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(80))  # user id / device id / system
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)


class UsageCost(Base):
    __tablename__ = "usage_costs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # stt | llm | tts
    units: Mapped[float] = mapped_column(Float)    # seconds of audio / tokens
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
