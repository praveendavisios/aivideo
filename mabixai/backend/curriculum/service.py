"""Curriculum ingestion: documents → structured node tree + RAG chunks."""
import logging
import os
import threading
import traceback

from sqlalchemy import delete, select

from ..db import session_scope
from ..models import Chunk, Curriculum, CurriculumNode, Document
from . import extraction, rag, structuring

log = logging.getLogger("mabcat.curriculum")


def _add_node(db, cur, parent_id, level, title, pos, sec=None, doc=None):
    n = CurriculumNode(curriculum_id=cur.id, parent_id=parent_id, level=level, title=title[:290], position=pos)
    if sec:
        src = {"document_id": doc.id, "filename": doc.filename}
        n.objectives = sec["objectives"]
        n.definitions = [d | src for d in sec["definitions"]]
        n.examples = [e | src for e in sec["examples"]]
        n.activities = [a | src for a in sec["activities"]]
        n.questions = [q | src for q in sec["questions"]]
        n.sources = [src | {"page": sec["page"]}]
    db.add(n)
    db.flush()
    return n


def _root(db, cur, meta):
    """subject → grade nodes (created once per curriculum)."""
    subject = db.scalar(select(CurriculumNode).where(CurriculumNode.curriculum_id == cur.id,
                                                     CurriculumNode.level == "subject"))
    if not subject:
        subject = _add_node(db, cur, None, "subject", cur.subject or meta.get("subject") or "General", 0)
    grade = db.scalar(select(CurriculumNode).where(CurriculumNode.parent_id == subject.id, CurriculumNode.level == "grade"))
    if not grade:
        g = cur.grade or meta.get("grade") or ""
        grade = _add_node(db, cur, subject.id, "grade", f"Grade {g}" if g else "All grades", 0)
    return grade


def ingest_document(db, cur, doc):
    pages = extraction.extract(doc.path)
    doc.pages = len(pages)
    methods = {p["method"] for p in pages if p["text"].strip()}
    doc.method = "mixed" if len(methods) > 1 else (methods.pop() if methods else "")
    outline = structuring.structure(pages, title_hint=os.path.splitext(doc.filename)[0].replace("_", " "))
    meta = outline["meta"]
    if not cur.subject and meta.get("subject"):
        cur.subject = meta["subject"]
    if not cur.grade and meta.get("grade"):
        cur.grade = meta["grade"]
    grade = _root(db, cur, meta)
    base = db.query(CurriculumNode).filter(CurriculumNode.parent_id == grade.id).count()
    n_nodes = n_chunks = 0

    def walk(sec, parent_id, pos):
        nonlocal n_nodes, n_chunks
        node = _add_node(db, cur, parent_id, sec["level"], sec["title"], pos, sec, doc)
        n_nodes += 1
        for ch in structuring.chunk_section(sec):
            db.add(Chunk(curriculum_id=cur.id, document_id=doc.id, node_id=node.id, page=ch["page"], text=ch["text"]))
            n_chunks += 1
        for i, child in enumerate(sec["children"]):
            walk(child, node.id, i)

    for i, ch in enumerate(outline["chapters"]):
        walk(ch, grade.id, base + i)
    db.flush()
    if rag.EMBEDDINGS:
        rows = db.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
        vecs = rag.embed([r.text for r in rows])
        if vecs:
            for r, v in zip(rows, vecs):
                r.embedding = v
    doc.status = "ready"
    return {"nodes": n_nodes, "chunks": n_chunks, "pages": len(pages), "method": doc.method}


def process_curriculum(curriculum_id, document_ids=None):
    with session_scope() as db:
        cur = db.get(Curriculum, curriculum_id)
        if not cur:
            return
        docs = [d for d in cur.documents if (document_ids is None or d.id in document_ids) and d.status != "ready"]
        errors = []
        for doc in docs:
            try:
                with db.begin_nested():
                    doc.status = "processing"
                    info = ingest_document(db, cur, doc)
                    log.info("ingested %s: %s", doc.filename, info)
            except Exception as exc:
                msg = str(exc) if isinstance(exc, extraction.ExtractionError) else f"{exc.__class__.__name__}: {exc}"
                log.error("ingest failed %s: %s\n%s", doc.filename, msg, traceback.format_exc())
                doc.status, doc.error = "failed", msg[:1000]
                errors.append(f"{doc.filename}: {msg}")
        ok = any(d.status == "ready" for d in cur.documents)
        cur.status = "ready" if ok else "failed"
        cur.error = "; ".join(errors)[:2000] if errors else None


def process_async(curriculum_id, document_ids=None):
    t = threading.Thread(target=process_curriculum, args=(curriculum_id, document_ids), daemon=True,
                         name=f"ingest-{curriculum_id}")
    t.start()
    return t


def delete_document_content(db, doc):
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))


def node_tree(db, curriculum_id):
    nodes = db.scalars(select(CurriculumNode).where(CurriculumNode.curriculum_id == curriculum_id)
                       .order_by(CurriculumNode.position)).all()
    by_parent = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    def build(n):
        return {"id": n.id, "level": n.level, "title": n.title,
                "counts": {"objectives": len(n.objectives or []), "definitions": len(n.definitions or []),
                           "questions": len(n.questions or []), "activities": len(n.activities or [])},
                "children": [build(c) for c in by_parent.get(n.id, [])]}
    return [build(n) for n in by_parent.get(None, [])]


def node_context(db, node):
    """The node's own text plus its children's (used for grounding lesson plans)."""
    ids = [node.id]
    frontier = [node.id]
    while frontier:
        kids = db.scalars(select(CurriculumNode.id).where(CurriculumNode.parent_id.in_(frontier))).all()
        ids += kids
        frontier = kids
    chunks = db.execute(select(Chunk, Document.filename).join(Document, Document.id == Chunk.document_id)
                        .where(Chunk.node_id.in_(ids)).order_by(Chunk.id)).all()
    return [{"chunk_id": c.id, "text": c.text, "source": {"document_id": c.document_id, "filename": fn, "page": c.page}}
            for c, fn in chunks]


def leaf_nodes_in_order(db, curriculum_id):
    """Teachable nodes (topics/subtopics, or chapters without children) in document order."""
    nodes = db.scalars(select(CurriculumNode).where(CurriculumNode.curriculum_id == curriculum_id)).all()
    kids = {}
    for n in nodes:
        kids.setdefault(n.parent_id, []).append(n)
    out = []

    def walk(n):
        children = sorted(kids.get(n.id, []), key=lambda x: x.position)
        if n.level in ("chapter", "topic", "subtopic") and (not children or n.level != "chapter"):
            out.append(n)
        for c in children:
            walk(c)
    for r in sorted(kids.get(None, []), key=lambda x: x.position):
        walk(r)
    return out
