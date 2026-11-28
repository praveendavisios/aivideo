"""Curriculum upload, structure, search, lesson plans, materials and assignments."""
import os
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..curriculum import extraction, lesson_planner, materials, rag, service
from ..db import get_db
from ..models import Assignment, Curriculum, CurriculumNode, Document, LessonPlan, Material, Student
from ..security import audit, current_user, get_student_for, require, visible_student_ids

router = APIRouter(prefix="/api/v1")
UPLOAD_DIR = os.path.join(settings.DATA_DIR, "documents")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _cur_out(c):
    return {"id": c.id, "title": c.title, "subject": c.subject, "grade": c.grade, "language": c.language,
            "status": c.status, "error": c.error, "created_at": c.created_at,
            "documents": [{"id": d.id, "filename": d.filename, "kind": d.kind, "pages": d.pages, "status": d.status,
                           "method": d.method, "error": d.error} for d in c.documents]}


def readable_curricula(db, user):
    if user.role == "admin":
        return None
    if user.role == "teacher":
        return set(db.scalars(select(Curriculum.id).where(Curriculum.owner_id == user.id)))
    ids = visible_student_ids(db, user) or {""}
    return set(db.scalars(select(Assignment.curriculum_id).where(Assignment.student_id.in_(ids))))


def get_curriculum(db, user, cid, write=False):
    c = db.get(Curriculum, cid)
    if not c:
        raise HTTPException(404, "Curriculum not found")
    if user.role == "admin" or c.owner_id == user.id:
        return c
    if not write and cid in (readable_curricula(db, user) or set()):
        return c
    raise HTTPException(404, "Curriculum not found")


def get_node(db, user, node_id, write=False):
    n = db.get(CurriculumNode, node_id)
    if not n:
        raise HTTPException(404, "Node not found")
    get_curriculum(db, user, n.curriculum_id, write)
    return n


async def _save_upload(f: UploadFile, cur_id):
    ext = os.path.splitext(f.filename or "")[1].lower().lstrip(".")
    if ext not in settings.DOC_EXT:
        raise HTTPException(415, f"Unsupported file .{ext}. Allowed: {', '.join(sorted(settings.DOC_EXT))}")
    safe = "".join(ch for ch in os.path.basename(f.filename) if ch.isalnum() or ch in "._- ")[:120] or f"upload.{ext}"
    path = os.path.join(UPLOAD_DIR, f"{cur_id}_{os.urandom(4).hex()}_{safe}")
    size, limit = 0, settings.MAX_DOC_MB * 1024 * 1024
    with open(path, "wb") as out:
        while chunk := await f.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                os.remove(path)
                raise HTTPException(413, f"{f.filename} is larger than {settings.MAX_DOC_MB} MB")
            out.write(chunk)
    try:
        extraction.validate_magic(path, ext)
    except extraction.ExtractionError as exc:
        os.remove(path)
        raise HTTPException(422, str(exc))
    return path, ext, safe


@router.post("/curricula", tags=["curriculum"])
async def upload_curriculum(title: str = Form(...), subject: str = Form(""), grade: str = Form(""),
                            language: str = Form("en"), files: list[UploadFile] = File(...),
                            db: Session = Depends(get_db), user=Depends(require("teacher", "parent"))):
    """Upload one or more documents (PDF, DOCX, PPTX, TXT, images). Processing runs in the background."""
    c = Curriculum(owner_id=user.id, title=title[:200], subject=subject[:100], grade=grade[:20], language=language[:10])
    db.add(c)
    db.flush()
    for f in files[:20]:
        path, ext, name = await _save_upload(f, c.id)
        db.add(Document(curriculum_id=c.id, filename=name, kind=ext, path=path))
    audit(db, user.id, "curriculum.upload", c.id, files=len(files))
    db.commit()
    service.process_async(c.id)
    return _cur_out(c)


@router.post("/curricula/{cid}/documents", tags=["curriculum"])
async def add_documents(cid: str, files: list[UploadFile] = File(...), db: Session = Depends(get_db), user=Depends(current_user)):
    c = get_curriculum(db, user, cid, write=True)
    ids = []
    for f in files[:20]:
        path, ext, name = await _save_upload(f, c.id)
        d = Document(curriculum_id=c.id, filename=name, kind=ext, path=path)
        db.add(d)
        db.flush()
        ids.append(d.id)
    c.status = "processing"
    db.commit()
    service.process_async(c.id, ids)
    return _cur_out(c)


@router.get("/curricula", tags=["curriculum"])
def list_curricula(db: Session = Depends(get_db), user=Depends(current_user)):
    ids = readable_curricula(db, user)
    q = select(Curriculum) if ids is None else select(Curriculum).where(Curriculum.id.in_(ids or {""}))
    return [_cur_out(c) for c in db.scalars(q.order_by(Curriculum.created_at.desc()))]


@router.get("/curricula/{cid}", tags=["curriculum"])
def curriculum_detail(cid: str, db: Session = Depends(get_db), user=Depends(current_user)):
    c = get_curriculum(db, user, cid)
    return _cur_out(c) | {"tree": service.node_tree(db, cid)}


@router.delete("/curricula/{cid}", tags=["curriculum"])
def delete_curriculum(cid: str, db: Session = Depends(get_db), user=Depends(current_user)):
    c = get_curriculum(db, user, cid, write=True)
    for d in c.documents:
        if os.path.exists(d.path):
            os.remove(d.path)
    db.delete(c)
    audit(db, user.id, "curriculum.delete", cid)
    db.commit()
    return {"ok": True}


@router.get("/curricula/{cid}/search", tags=["curriculum"])
def search(cid: str, q: str, k: int = 5, db: Session = Depends(get_db), user=Depends(current_user)):
    get_curriculum(db, user, cid)
    return {"query": q, "results": rag.search(db, [cid], q, k=min(k, 20))}


@router.get("/nodes/{node_id}", tags=["curriculum"])
def node_detail(node_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    n = get_node(db, user, node_id)
    plans = db.scalars(select(LessonPlan).where(LessonPlan.node_id == n.id).order_by(LessonPlan.updated_at.desc())).all()
    mats = db.scalars(select(Material).where(Material.node_id == n.id).order_by(Material.created_at.desc())).all()
    return {"id": n.id, "title": n.title, "level": n.level, "curriculum_id": n.curriculum_id, "objectives": n.objectives,
            "definitions": n.definitions, "examples": n.examples, "activities": n.activities, "questions": n.questions,
            "sources": n.sources, "plans": [_plan_out(p) for p in plans],
            "materials": [_mat_out(materials.refresh_video_status(m)) for m in mats]}


class NodeEdit(BaseModel):
    title: str | None = None
    objectives: list[str] | None = None
    prerequisites: list[str] | None = None


@router.patch("/nodes/{node_id}", tags=["curriculum"])
def edit_node(node_id: str, body: NodeEdit, db: Session = Depends(get_db), user=Depends(require("teacher"))):
    n = get_node(db, user, node_id, write=True)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(n, k, v)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ plans
def _plan_out(p):
    return {"id": p.id, "node_id": p.node_id, "status": p.status, "persona": p.persona, "generator": p.generator,
            "content": p.content, "updated_at": p.updated_at}


@router.post("/nodes/{node_id}/plans", tags=["lesson plans"])
def generate_plan(node_id: str, persona: str | None = None, db: Session = Depends(get_db), user=Depends(require("teacher", "parent"))):
    n = get_node(db, user, node_id, write=True)
    c = db.get(Curriculum, n.curriculum_id)
    content, gen, persona = lesson_planner.generate(db, n, persona, c.grade, c.language, c.subject)
    p = LessonPlan(node_id=n.id, persona=persona, content=content, generator=gen)
    db.add(p)
    audit(db, user.id, "plan.generate", p.id, generator=gen)
    db.commit()
    return _plan_out(p)


class PlanEdit(BaseModel):
    content: dict | None = None
    status: str | None = None


@router.patch("/plans/{plan_id}", tags=["lesson plans"])
def edit_plan(plan_id: str, body: PlanEdit, db: Session = Depends(get_db), user=Depends(require("teacher", "parent"))):
    p = db.get(LessonPlan, plan_id)
    if not p:
        raise HTTPException(404, "Plan not found")
    get_node(db, user, p.node_id, write=True)
    if body.content is not None:
        p.content, p.edited_by = body.content, user.id
    if body.status:
        if body.status not in ("draft", "approved"):
            raise HTTPException(400, "status must be draft or approved")
        p.status = body.status
    audit(db, user.id, "plan.edit", p.id, status=p.status)
    db.commit()
    return _plan_out(p)


# -------------------------------------------------------------- materials
def _mat_out(m):
    return {"id": m.id, "node_id": m.node_id, "kind": m.kind, "status": m.status, "content": m.content,
            "has_file": bool(m.path), "studio_video_id": m.studio_video_id, "error": m.error}


@router.post("/nodes/{node_id}/materials", tags=["materials"])
def create_material(node_id: str, kind: str, device_id: str | None = None, db: Session = Depends(get_db),
                    user=Depends(require("teacher", "parent"))):
    n = get_node(db, user, node_id, write=True)
    if kind in ("flashcards", "worksheet", "quiz"):
        m = materials.create_text_material(db, n, kind)
    elif kind in ("illustration", "diagram"):
        m = materials.create_illustration(db, n, device_id)
    elif kind in ("video", "story_video"):
        m = materials.create_video(db, n, "story" if kind == "story_video" else "answer")
    else:
        raise HTTPException(400, "kind must be flashcards, worksheet, quiz, illustration, video or story_video")
    db.commit()
    return _mat_out(m)


@router.get("/materials/{material_id}", tags=["materials"])
def material(material_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "Material not found")
    get_node(db, user, m.node_id)
    materials.refresh_video_status(m)
    db.commit()
    return _mat_out(m)


@router.get("/materials/{material_id}/file", tags=["materials"])
def material_file(material_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    m = db.get(Material, material_id)
    if not m or not m.path or not os.path.exists(m.path):
        raise HTTPException(404, "File not ready")
    get_node(db, user, m.node_id)
    return FileResponse(m.path)


# ------------------------------------------------------------ assignments
@router.post("/assignments", tags=["assignments"])
def assign(student_id: str, curriculum_id: str, node_id: str | None = None, db: Session = Depends(get_db),
           user=Depends(require("teacher", "parent"))):
    get_curriculum(db, user, curriculum_id)
    st = get_student_for(db, user, student_id)
    if not db.scalar(select(Assignment).where(Assignment.student_id == st.id, Assignment.curriculum_id == curriculum_id,
                                              Assignment.node_id == node_id)):
        db.add(Assignment(student_id=st.id, curriculum_id=curriculum_id, node_id=node_id, assigned_by=user.id))
    audit(db, user.id, "assignment.create", st.id, curriculum=curriculum_id, node=node_id)
    db.commit()
    return {"ok": True}


@router.get("/students/{student_id}/assignments", tags=["assignments"])
def assignments(student_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    st = get_student_for(db, user, student_id)
    out = []
    for a in db.scalars(select(Assignment).where(Assignment.student_id == st.id)):
        c = db.get(Curriculum, a.curriculum_id)
        out.append({"curriculum_id": a.curriculum_id, "title": c.title if c else "?", "node_id": a.node_id,
                    "lessons": [{"node_id": n.id, "title": n.title, "level": n.level}
                                for n in service.leaf_nodes_in_order(db, a.curriculum_id)]})
    return out
