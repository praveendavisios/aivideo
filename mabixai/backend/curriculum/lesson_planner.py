"""Teaching-plan generation for a curriculum node.

LLM mode: grounded on the node's own chunks; the model must cite [S#] and
mark gaps instead of inventing syllabus content.
Offline mode: assembled strictly from what extraction found in the document
(objectives, definitions, examples, activities, questions).
"""
import logging
import re

from ..teaching import llm
from ..teaching.personas import persona_for_subject, system_prompt
from . import service

log = logging.getLogger("mabcat.planner")

PLAN_KEYS = ["introduction", "explanation", "real_life_examples", "story", "questions", "common_misunderstandings",
             "activities", "revision_questions", "assessment_criteria"]


def _sources(ctx, limit=8):
    return ctx[:limit]


def offline_plan(node, ctx):
    defs = node.definitions or []
    qs = []
    for q in node.questions or []:
        qs.append({"question": q["question"], "expected_answer": q.get("answer", ""), "source": {"page": q.get("page"), "filename": q.get("filename")}})
    for d in defs:
        if len(qs) >= 6:
            break
        qs.append({"question": f"What is {d['term'].lower()}?", "expected_answer": d["meaning"],
                   "source": {"page": d.get("page"), "filename": d.get("filename")}})
    # Explanation = explanatory sentences only: no questions, answers, bullets or activity instructions.
    skip = {re.sub(r"\W+", " ", x).strip().lower() for x in
            [q["question"] for q in node.questions or []] + [q.get("answer", "") for q in node.questions or []] +
            [a["text"] for a in node.activities or []] + list(node.objectives or [])}
    sentences = []
    for c in ctx:
        body = re.sub(r"^[^:]{1,120}:\s+", "", c["text"])  # drop "Section title:" prefixes
        for sent in re.split(r"(?<=[.!?])\s+|\s+-\s+", body):
            sent = sent.strip(" -")
            key = re.sub(r"\W+", " ", sent).strip().lower()
            if (len(sent.split()) > 4 and not sent.endswith("?") and not re.match(r"^(answer|ans)\b", sent, re.I)
                    and key not in skip and sent not in sentences):
                sentences.append(sent)
    explanation = " ".join(sentences[:4])
    return {
        "introduction": f"Today we are going to learn about {node.title}!",
        "explanation": explanation,
        "key_terms": [{"term": d["term"], "meaning": d["meaning"]} for d in defs],
        "real_life_examples": [e["text"] for e in (node.examples or [])][:4],
        "story": "",
        "questions": qs,
        "common_misunderstandings": [],
        "activities": [a["text"] for a in (node.activities or [])][:4],
        "revision_questions": [q["question"] for q in qs[:3]],
        "assessment_criteria": node.objectives or [],
        "learning_objectives": node.objectives or [],
        "gaps": [k for k, v in (("explanation", explanation), ("questions", qs)) if not v],
        "sources": [c["source"] for c in _sources(ctx)],
    }


def llm_plan(node, ctx, persona, grade, language):
    excerpts = "\n\n".join(f"[S{i + 1}] (p.{c['source']['page']}) {c['text']}" for i, c in enumerate(_sources(ctx)))
    found = {"objectives": node.objectives, "definitions": [f"{d['term']}: {d['meaning']}" for d in node.definitions or []],
             "questions_in_book": [q["question"] for q in node.questions or []]}
    prompt = f"""Prepare a teaching plan for the topic "{node.title}" (grade {grade or 'unknown'}).
Write it in {language or 'English'}.

Curriculum excerpts (the ONLY source of syllabus facts):
{excerpts or '(no excerpts found)'}

Already extracted from the document: {found}

Return JSON with exactly these keys:
{{"introduction": str, "explanation": str (simple, cite [S#]),
  "key_terms": [{{"term": str, "meaning": str}}],
  "real_life_examples": [str], "story": str (short story explaining the idea),
  "questions": [{{"question": str, "expected_answer": str, "hint": str, "source": "S#"}}] (4-6, easy→harder),
  "common_misunderstandings": [str], "activities": [str] (safe, simple),
  "revision_questions": [str], "assessment_criteria": [str], "learning_objectives": [str],
  "gaps": [str] (what the excerpts do NOT cover that a teacher should add)}}
Do not invent facts that are not supported by the excerpts; put such needs in "gaps"."""
    data = llm.chat_json([{"role": "system", "content": system_prompt(persona)}, {"role": "user", "content": prompt}])
    plan = {k: data.get(k, [] if k != "introduction" and k not in ("explanation", "story") else "") for k in PLAN_KEYS}
    plan["key_terms"] = data.get("key_terms", [])
    plan["learning_objectives"] = data.get("learning_objectives") or node.objectives or []
    plan["gaps"] = data.get("gaps", [])
    srcs = _sources(ctx)
    for q in plan["questions"]:
        m = re.match(r"S(\d+)", str(q.get("source", "")))
        q["source"] = srcs[int(m.group(1)) - 1]["source"] if m and int(m.group(1)) <= len(srcs) else None
    plan["sources"] = [c["source"] for c in srcs]
    return plan


def generate(db, node, persona=None, grade="", language="en", subject=""):
    ctx = service.node_context(db, node)
    persona = persona or persona_for_subject(subject)
    if llm.available():
        try:
            return llm_plan(node, ctx, persona, grade, language), f"llm:{llm.provider()}:{llm.model_name()}", persona
        except llm.LLMUnavailable as exc:
            log.warning("LLM plan failed, using offline plan: %s", exc)
    return offline_plan(node, ctx), "offline", persona
