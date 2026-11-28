"""AI Teacher engine and the teacher–student interaction loop.

Loop: introduce topic → explain → ask question → student answers (voice/touch)
→ evaluate → friendly feedback → if wrong: hint / re-explain with another
example and ask again → next question → save progress → summary.

The engine is transport-independent: every call returns a list of outgoing
protocol messages (dicts); the WebSocket / REST layers deliver them.

Two modes, chosen per call and reported in every response's "engine" field:
  llm      — Ollama / OpenAI / Gemini, grounded on curriculum excerpts with [S#] citations
  offline  — deterministic, uses only content extracted from the uploaded documents
"""
import json
import logging
import random
import re

from sqlalchemy import select

from ..curriculum import lesson_planner, rag
from ..models import CurriculumNode, Curriculum, LessonPlan, Progress, Turn
from ..speech.language import code_switching
from . import llm, student_engine
from .personas import persona_for_subject, system_prompt

log = logging.getLogger("mabcat.teacher")
HISTORY = 10
EXPRESSIONS = {  # emotion → robot expression + ear angles (0 = down, 90 = straight up)
    "happy": ("happy", 70, 70), "excited": ("excited", 85, 85), "thinking": ("thinking", 45, 60),
    "encouraging": ("happy", 60, 60), "empathetic": ("concerned", 35, 35), "listening": ("listening", 25, 25),
    "speaking": ("speaking", 60, 60), "neutral": ("neutral", 50, 50), "sleeping": ("sleeping", 5, 5),
}
DONT_KNOW = re.compile(r"\b(i\s*(do\s*n[o']?t|dont)\s*know|no idea|not sure|i forgot|അറിയില്ല|पता नहीं|मालूम नहीं|தெரியாது|ಗೊತ್ತಿಲ್ಲ)\b", re.I)
QUESTION_START = re.compile(r"^(what|why|how|when|where|who|which|can you|could you|tell me|explain|is it|are|does|do)\b", re.I)
PRAISE = ["Great job", "Wonderful", "Well done", "Super", "Excellent thinking"]
TRY_AGAIN = ["Good try", "Nice effort", "You're close", "Almost there"]


def expression(emotion):
    e, l, r = EXPRESSIONS.get(emotion, EXPRESSIONS["neutral"])
    return {"type": "robot_expression", "expression": e, "ear_left": l, "ear_right": r}


def _tokens(text):
    return {t for t in rag.tokenize(text) if len(t) > 2 or not t.isascii()}


# ----------------------------------------------------------- evaluation
def evaluate_offline(answer, expected, question=""):
    """Key-word overlap with the curriculum's reference answer.

    Words already in the question don't count (for "What is stomata?" saying "stomata" proves nothing).
    Long book definitions need ~a third of their key words; short answers need half.
    """
    exp = _tokens(expected) - _tokens(question)
    if not exp:
        return {"score": 0.5, "correct": True, "evaluator": "ungraded",
                "note": "No reference answer in the curriculum — answer accepted without grading."}
    got = _tokens(answer)
    overlap = len(exp & got)
    needed = len(exp) if len(exp) <= 2 else max(2, -(-len(exp) * (5 if len(exp) <= 4 else 3) // 10))
    return {"score": round(min(1.0, overlap / needed), 2), "correct": overlap >= needed, "evaluator": "keyword-overlap"}


def evaluate_llm(question, expected, answer, student, persona, language):
    prompt = f"""A child (grade {student.grade or '?'}) answered a question. Grade it fairly and kindly.
Question: {question}
Reference answer from the curriculum: {expected or '(none — judge if the answer is reasonable)'}
Child's answer (original words, may mix languages): {answer}

Return JSON: {{"score": 0.0-1.0, "correct": true/false, "feedback": "1-2 encouraging sentences to the child in the language they used",
"hint": "a hint that helps without giving the answer away", "misconception": "what the child may have misunderstood, or empty"}}
Accept answers that are correct in meaning even with spelling mistakes, simple words or another language."""
    data = llm.chat_json([{"role": "system", "content": system_prompt(persona, student)}, {"role": "user", "content": prompt}])
    score = float(data.get("score", 0) or 0)
    return {"score": max(0.0, min(1.0, score)), "correct": bool(data.get("correct", score >= 0.6)),
            "feedback": data.get("feedback", ""), "hint": data.get("hint", ""),
            "misconception": data.get("misconception", ""), "evaluator": f"llm:{llm.model_name()}"}


# --------------------------------------------------------------- engine
class TeacherEngine:
    def __init__(self, db, session, student):
        self.db, self.session, self.student = db, session, student
        self.state = dict(session.state or {})
        self.out = []

    # -- helpers
    def _curricula(self):
        ids = student_engine.curricula_for(self.db, self.student)
        if self.session.curriculum_id and self.session.curriculum_id not in ids:
            ids.append(self.session.curriculum_id)
        return ids

    def _persona(self):
        if self.state.get("persona"):
            return self.state["persona"]
        cur = self.db.get(Curriculum, self.session.curriculum_id) if self.session.curriculum_id else None
        return self.student.persona if self.student.persona != "friendly" or not cur else persona_for_subject(cur.subject)

    def _history(self):
        rows = self.db.scalars(select(Turn).where(Turn.session_id == self.session.id).order_by(Turn.id.desc())
                               .limit(HISTORY)).all()
        return [{"role": "assistant" if t.role == "teacher" else "user", "content": t.text} for t in reversed(rows)]

    def say(self, text, emotion="happy", engine="offline", sources=None, kind="teacher_response", **extra):
        msg = {"type": kind, "session_id": self.session.id, "text": text.strip(), "emotion": emotion,
               "engine": engine, "sources": sources or [], **extra}
        self.out.append(expression(emotion))
        self.out.append(msg)
        self.db.add(Turn(session_id=self.session.id, role="teacher", text=msg["text"],
                         meta={"emotion": emotion, "engine": engine, "sources": msg["sources"], "kind": kind}))
        return msg

    def _save(self):
        self.session.state = self.state
        self.db.flush()

    def _plan(self, node):
        plan = self.db.scalar(select(LessonPlan).where(LessonPlan.node_id == node.id, LessonPlan.status == "approved")
                              .order_by(LessonPlan.updated_at.desc()))
        plan = plan or self.db.scalar(select(LessonPlan).where(LessonPlan.node_id == node.id)
                                      .order_by(LessonPlan.updated_at.desc()))
        if not plan:
            cur = self.db.get(Curriculum, node.curriculum_id)
            content, gen, persona = lesson_planner.generate(self.db, node, None, cur.grade, cur.language, cur.subject)
            plan = LessonPlan(node_id=node.id, persona=persona, content=content, generator=gen)
            self.db.add(plan)
            self.db.flush()
        return plan

    # -- lesson flow
    def start_lesson(self, node_id):
        node = self.db.get(CurriculumNode, node_id)
        if not node or node.curriculum_id not in self._curricula():
            self.out.append({"type": "error", "code": "node_not_found", "message": "That lesson is not assigned to this student."})
            return self.out
        plan = self._plan(node)
        c = plan.content
        self.session.node_id, self.session.curriculum_id = node.id, node.curriculum_id
        progress = self.db.scalar(select(Progress).where(Progress.student_id == self.student.id, Progress.node_id == node.id))
        diff = student_engine.difficulty(self.student, progress)
        qs = list(c.get("questions") or [])
        if diff == 1:
            qs = qs[:4]
        self.state = {"mode": "lesson", "phase": "awaiting_answer" if qs else "done", "node_id": node.id,
                      "plan_id": plan.id, "persona": plan.persona, "q_index": 0, "attempts": 0, "questions": qs,
                      "correct": 0, "difficulty": diff}
        self.out.append({"type": "lesson_update", "session_id": self.session.id, "node_id": node.id, "title": node.title,
                         "plan_id": plan.id, "plan_generator": plan.generator, "questions": len(qs),
                         "objectives": c.get("learning_objectives", []), "step": "introduction"})
        name = self.student.display_name
        intro = c.get("introduction") or f"Today we are going to learn about {node.title}!"
        self.say(f"Hi {name}! {intro}", "excited", plan.generator, c.get("sources", [])[:2])
        explanation = c.get("story") if self._persona() == "storyteller" and c.get("story") else c.get("explanation")
        if explanation:
            self.say(explanation, "speaking", plan.generator, c.get("sources", [])[:3])
        if c.get("real_life_examples"):
            self.say(f"Here's an example: {c['real_life_examples'][0]}", "happy", plan.generator)
        if not (explanation or c.get("real_life_examples")):
            self.say("I couldn't find an explanation for this topic in your uploaded lessons yet, so let's go "
                     "through what is there together.", "thinking", plan.generator)
        if qs:
            self._ask()
        else:
            self.say("Your teacher hasn't added questions for this topic yet. Ask me anything about it!", "happy")
            self.state["mode"] = "chat"
        self._save()
        return self.out

    def _ask(self):
        q = self.state["questions"][self.state["q_index"]]
        n, total = self.state["q_index"] + 1, len(self.state["questions"])
        self.out.append(expression("thinking"))
        msg = {"type": "quiz_question", "session_id": self.session.id, "question_index": n, "total": total,
               "text": q["question"], "input": "voice_or_touch", "options": q.get("options") or None}
        self.out.append(msg)
        self.db.add(Turn(session_id=self.session.id, role="teacher", text=q["question"], meta={"kind": "quiz_question"}))
        self.out.append(expression("listening"))

    def _next_question(self):
        self.state["q_index"] += 1
        self.state["attempts"] = 0
        if self.state["q_index"] < len(self.state["questions"]):
            self._ask()
            return
        self.state["phase"] = "done"
        self.state["mode"] = "chat"
        total, right = len(self.state["questions"]), self.state["correct"]
        node = self.db.get(CurriculumNode, self.state["node_id"])
        cheer = "You're a superstar!" if right == total else "You worked really hard today!" if right else \
            "Learning takes practice — we'll try again soon!"
        self.say(f"We finished {node.title}! You got {right} out of {total}. {cheer}", "excited",
                 kind="teacher_response", lesson_complete=True)
        nxt = student_engine.next_node(self.db, self.student)
        self.out.append({"type": "lesson_update", "session_id": self.session.id, "node_id": node.id, "step": "completed",
                         "score": f"{right}/{total}", "next": nxt,
                         "revision": student_engine.revision_list(self.db, self.student)})

    def _hint(self, q, evaluation):
        if evaluation.get("hint"):
            return evaluation["hint"]
        exp = q.get("expected_answer", "")
        words = exp.split()
        if len(words) > 3:
            return f"Think about this: it starts with \"{' '.join(words[:max(2, len(words) // 3)])}…\""
        return "Think about what we just learned. Take your time!"

    def answer(self, text, language=None):
        q = self.state["questions"][self.state["q_index"]]
        if DONT_KNOW.search(text):
            self.state["attempts"] += 1
            if self.state["attempts"] >= 2:
                return self._reveal(q, "That's okay! Let's learn it together.")
            self.say(f"That's okay, it's fine not to know yet! {self._hint(q, {})}", "encouraging")
            self.out.append(expression("listening"))
            return
        persona, engine = self._persona(), "offline"
        ev = None
        if llm.available():
            try:
                ev = evaluate_llm(q["question"], q.get("expected_answer", ""), text, self.student, persona, language)
                engine = ev["evaluator"]
            except llm.LLMUnavailable as exc:
                log.warning("LLM evaluation failed: %s", exc)
        ev = ev or evaluate_offline(text, q.get("expected_answer", ""), q["question"])
        self.state["attempts"] += 1
        student_engine.record_attempt(self.db, self.session, self.student, self.state["node_id"], q["question"], text,
                                      q.get("expected_answer", ""), ev["score"], ev["correct"], ev["evaluator"])
        self.out.append({"type": "answer_evaluation", "session_id": self.session.id, "question_index": self.state["q_index"] + 1,
                         "score": ev["score"], "correct": ev["correct"], "evaluator": ev["evaluator"],
                         "attempt": self.state["attempts"]})
        if ev["correct"]:
            self.state["correct"] += 1
            fb = ev.get("feedback") or f"{random.choice(PRAISE)}, {self.student.display_name}! " + (
                f"Yes — {q['expected_answer']}" if q.get("expected_answer") and ev["evaluator"] != "ungraded" else
                "Thanks for sharing your answer!")
            self.say(fb, "happy", engine)
            self._next_question()
        elif self.state["attempts"] < 2:
            fb = ev.get("feedback") or f"{random.choice(TRY_AGAIN)}! {self._hint(q, ev)}"
            self.say(fb, "encouraging", engine)
            self._reteach(q, ev)
            self.out.append(expression("listening"))
        else:
            self._reveal(q, ev.get("feedback") or "Good effort! Let's look at the answer together.")

    def _reveal(self, q, lead):
        exp = q.get("expected_answer") or "Let's ask your teacher about this one."
        self.say(f"{lead} The answer is: {exp}", "empathetic")
        self._next_question()

    def _reteach(self, q, ev):
        """Explain again differently: another example from the curriculum or the LLM."""
        node = self.db.get(CurriculumNode, self.state["node_id"])
        if llm.available():
            hits = rag.search(self.db, self._curricula(), q["question"], k=3, node_id=node.id)
            ctx = "\n".join(f"[S{i + 1}] {h['text']}" for i, h in enumerate(hits))
            try:
                data = llm.chat_json([
                    {"role": "system", "content": system_prompt(self._persona(), self.student)},
                    {"role": "user", "content": f"The child is stuck on: {q['question']}\nMisunderstanding: "
                     f"{ev.get('misconception') or 'unknown'}\nCurriculum:\n{ctx}\nExplain the idea again in a DIFFERENT way "
                     f"(new everyday example, 2-3 sentences) WITHOUT giving the answer, then say you'll ask again. "
                     f'Return JSON {{"text": str}}'}])
                self.say(data.get("text", ""), "speaking", f"llm:{llm.model_name()}", rag.cite(hits))
                return
            except llm.LLMUnavailable:
                pass
        examples = [e["text"] for e in (node.examples or [])]
        if examples:
            self.say(f"Let me explain another way: {random.choice(examples)} Now, try again!", "speaking")
        else:
            self.say("Try again — I'll wait!", "encouraging")

    # -- free conversation
    def chat(self, text, language=None):
        node_id = self.state.get("node_id")
        hits = rag.search(self.db, self._curricula(), text, k=4, node_id=node_id)
        if llm.available():
            ctx = "\n\n".join(f"[S{i + 1}] ({h['source']['filename']} p.{h['source']['page']}) {h['text']}" for i, h in enumerate(hits))
            lang_note = code_switching.describe(language) if language else ""
            msgs = [{"role": "system", "content": system_prompt(self._persona(), self.student)}] + self._history() + [{
                "role": "user", "content": f"Curriculum excerpts:\n{ctx or '(none found)'}\n\nChild says ({lang_note}): {text}\n\n"
                f"Reply as Mab Cat in 1-4 short sentences. If the excerpts don't cover it, say it is not in their lessons "
                f"and give only a very simple, safe general answer. End with a small question to keep them thinking. "
                f'Return JSON {{"text": str, "emotion": "happy|excited|thinking|encouraging|empathetic", "used_sources": ["S1"], "in_curriculum": bool}}'}]
            try:
                data = llm.chat_json(msgs)
                used = [int(s[1:]) - 1 for s in data.get("used_sources", []) if re.fullmatch(r"S\d+", str(s))]
                srcs = [hits[i]["source"] for i in used if 0 <= i < len(hits)]
                self.say(data.get("text", ""), data.get("emotion", "happy"), f"llm:{llm.model_name()}", srcs,
                         in_curriculum=bool(data.get("in_curriculum", bool(srcs))))
                return
            except llm.LLMUnavailable as exc:
                log.warning("LLM chat failed: %s", exc)
        if hits:
            q = _tokens(text)
            cands = []
            for h in hits[:3]:
                body = re.sub(r"^[^:]{1,120}:\s+", "", h["text"])  # drop the "Section title: " chunk prefix
                for sent in re.split(r"(?<=[.!?])\s+|\s+-\s+", body):
                    sent = sent.strip(" -")
                    if len(sent.split()) < 5 or not sent[-1:] in ".!?":
                        continue  # headings, bullets, fragments
                    score = len(_tokens(sent) & q) + (1.5 if re.search(r"\b(is|are|means)\b", sent) else 0) + h["score"]
                    cands.append((score, sent, h["source"]))
            cands.sort(key=lambda c: -c[0])
            if cands:
                picked = cands[:2]
                self.say("Here's what your lesson says: " + " ".join(c[1] for c in picked), "happy", "offline",
                         list({json.dumps(c[2], sort_keys=True): c[2] for c in picked}.values()), in_curriculum=True)
            else:
                self.say(f"Here's what your lesson says: {hits[0]['text'][:300]}", "happy", "offline", [hits[0]["source"]],
                         in_curriculum=True)
        else:
            self.say("Hmm, I couldn't find that in your lessons yet. Let's ask your teacher, or pick a lesson to learn "
                     "together!", "thinking", "offline", in_curriculum=False)

    # -- entry point
    def student_message(self, text, language=None, meta=None):
        text = (text or "").strip()
        if not text:
            return self.out
        lang = language or code_switching.label(text)  # e.g. "ml-en" keeps the mix
        self.db.add(Turn(session_id=self.session.id, role="student", text=text, language=lang, meta=meta or {}))
        self.out.append(expression("thinking"))
        awaiting = self.state.get("mode") == "lesson" and self.state.get("phase") == "awaiting_answer"
        if awaiting and (text.endswith("?") or QUESTION_START.match(text)) and not DONT_KNOW.search(text):
            self.chat(text, lang)  # a question instead of an answer: help, then repeat the pending question
            q = self.state["questions"][self.state["q_index"]]
            self.say(f"Now, back to our question: {q['question']}", "encouraging", kind="teacher_response")
            self.out.append(expression("listening"))
        elif awaiting:
            self.answer(text, lang)
        else:
            self.chat(text, lang)
        self._save()
        return self.out
