"""Teaching materials for a curriculum node.

Text materials (flashcards, worksheet, quiz) are built from the lesson plan and
the node's extracted content; with an LLM available, quizzes get extra
plausible distractors. Illustrations reuse the studio's GPU image pipeline
(mabix.pipelines.models) and videos are created as Short-Answer / Story jobs in
the existing Mabix video studio — the same queue, library and renderer.
"""
import logging
import os
import random
import threading

from ..config import settings
from ..db import session_scope
from ..models import CurriculumNode, LessonPlan, Material
from ..teaching import llm

log = logging.getLogger("mabcat.materials")
MEDIA_DIR = os.path.join(settings.DATA_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
SAFE_IMG = ("children's educational illustration, friendly, colourful, simple shapes, clear labels, "
            "safe for kids, no text errors")
NEG_IMG = "scary, violent, gore, weapon, adult, nsfw, blurry, distorted, watermark"
notify = None  # set by main: callable(device_id|None, message) for media_ready events


def _plan(db, node):
    from sqlalchemy import select
    return db.scalar(select(LessonPlan).where(LessonPlan.node_id == node.id)
                     .order_by(LessonPlan.status.desc(), LessonPlan.updated_at.desc()))


def flashcards(node, plan):
    cards = [{"front": t["term"], "back": t["meaning"]} for t in (plan.get("key_terms") if plan else []) or []]
    for d in node.definitions or []:
        if not any(c["front"].lower() == d["term"].lower() for c in cards):
            cards.append({"front": d["term"], "back": d["meaning"], "source": {"page": d.get("page"), "filename": d.get("filename")}})
    return {"title": f"{node.title} — flashcards", "cards": cards}


def worksheet(node, plan):
    qs = [q["question"] for q in (plan or {}).get("questions", [])] or [q["question"] for q in node.questions or []]
    acts = (plan or {}).get("activities") or [a["text"] for a in node.activities or []]
    return {"title": f"{node.title} — worksheet", "sections": [
        {"heading": "Answer these questions", "items": qs},
        {"heading": "Try this activity", "items": acts},
        {"heading": "Draw and label", "items": [f"Draw a picture that shows {node.title.lower()}."]}]}


def quiz(node, plan, n=5):
    qs = list((plan or {}).get("questions") or [])
    if not qs:
        qs = [{"question": q["question"], "expected_answer": q.get("answer", "")} for q in node.questions or []]
    answers = [q.get("expected_answer") for q in qs if q.get("expected_answer")]
    items = []
    for q in qs[:n]:
        item = {"question": q["question"], "answer": q.get("expected_answer", ""), "type": "open"}
        others = [a for a in answers if a != item["answer"]]
        if item["answer"] and len(others) >= 2:  # multiple choice from other answers in the same lesson
            opts = random.sample(others, 2) + [item["answer"]]
            random.shuffle(opts)
            item.update(type="mcq", options=opts, correct_index=opts.index(item["answer"]))
        items.append(item)
    if llm.available() and items:
        try:
            data = llm.chat_json([{"role": "user", "content":
                "For each question write 3 short wrong-but-plausible options for children. Return JSON "
                '{"items": [{"question": str, "distractors": [str, str, str]}]}\n' +
                "\n".join(f"- {i['question']} (answer: {i['answer']})" for i in items if i["answer"])}])
            by_q = {d["question"]: d.get("distractors", []) for d in data.get("items", [])}
            for i in items:
                ds = by_q.get(i["question"])
                if ds and i["answer"]:
                    opts = ds[:3] + [i["answer"]]
                    random.shuffle(opts)
                    i.update(type="mcq", options=opts, correct_index=opts.index(i["answer"]), distractors_by="llm")
        except llm.LLMUnavailable:
            pass
    return {"title": f"{node.title} — quiz", "items": items}


def create_text_material(db, node, kind):
    plan = _plan(db, node)
    content = {"flashcards": flashcards, "worksheet": worksheet, "quiz": quiz}[kind](node, plan.content if plan else None)
    m = Material(node_id=node.id, kind=kind, status="ready", content=content)
    db.add(m)
    db.flush()
    return m


def _illustrate(material_id, device_id):
    with session_scope() as db:
        m = db.get(Material, material_id)
        node = db.get(CurriculumNode, m.node_id)
        plan = _plan(db, node)
        idea = (plan.content.get("real_life_examples") or [""])[0] if plan else ""
        prompt = f"{SAFE_IMG}, {node.title}, {idea}"[:400]
        m.status, m.content = "processing", {"prompt": prompt}
    try:
        from mabix.pipelines import models
        path = os.path.join(MEDIA_DIR, f"{material_id}.png")
        models.generate_image(prompt, path, width=768, height=512, negative=NEG_IMG, seed=hash(material_id) % 10000,
                              base_model=models.model_setting("stylized", "Lykon/dreamshaper-8"))
        with session_scope() as db:
            m = db.get(Material, material_id)
            m.status, m.path = "ready", path
        if notify:
            notify(device_id, {"type": "media_ready", "kind": "illustration", "material_id": material_id,
                               "url": f"/api/v1/materials/{material_id}/file"})
    except Exception as exc:
        log.exception("illustration failed")
        with session_scope() as db:
            m = db.get(Material, material_id)
            m.status, m.error = "failed", f"{exc.__class__.__name__}: {exc}"[:1000]


def create_illustration(db, node, device_id=None):
    m = Material(node_id=node.id, kind="illustration", status="pending")
    db.add(m)
    db.flush()
    threading.Thread(target=_illustrate, args=(m.id, device_id), daemon=True).start()
    return m


def create_video(db, node, style="answer"):
    """Queue an educational video in the existing Mabix video studio."""
    from mabix import jobs, repo
    from mabix.pipelines import studio
    plan = _plan(db, node)
    c = plan.content if plan else {}
    brief = (f"Explain '{node.title}' for children. " + (c.get("explanation") or "") +
             " Key ideas: " + "; ".join(t.get("term", "") for t in c.get("key_terms", []))).strip()[:3500]
    vtype = "story" if style == "story" else "answer"
    cfg = studio.build_config(vtype, {"review": "0", "duration": 60, "style": "educational" if vtype == "answer" else "storybook"},
                              repo.get_setting("defaults", {}))
    vid = repo.create_video(vtype, "ai", prompt=brief, config=cfg, status="scripting", category="mabcat")
    sj = jobs.submit("script", vid, {}, brief[:200], allow_duplicate=True)
    jobs.submit("render", vid, {}, brief[:200], parent_job_id=sj, allow_duplicate=True)
    m = Material(node_id=node.id, kind="video", status="processing", studio_video_id=vid,
                 content={"studio_url": f"/videos/{vid}", "type": vtype})
    db.add(m)
    db.flush()
    return m


def refresh_video_status(m):
    if m.kind != "video" or not m.studio_video_id:
        return m
    from mabix import repo
    v = repo.get_video(m.studio_video_id)
    if not v:
        m.status, m.error = "failed", "Studio video was deleted"
    elif v["status"] == "completed":
        m.status = "ready"
        m.content = {**(m.content or {}), "media_url": f"/media/video/{v['id']}", "duration": v.get("duration")}
    elif v["status"] == "failed":
        m.status, m.error = "failed", v.get("error")
    return m
