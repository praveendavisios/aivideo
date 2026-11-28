"""Turn extracted pages into a curriculum outline.

Heuristic, source-grounded structuring (works without an LLM):
  chapter  ← "Chapter/Unit/Lesson/Module N", "# Heading"
  topic    ← "N.N Title", "## Heading", short ALL-CAPS lines
  subtopic ← "N.N.N Title", "### Heading"
Within each section it collects learning objectives, definitions, examples,
activities and question/answer pairs — only from text that is actually in
the document, each with its page reference.
"""
import re

CHAPTER_RE = re.compile(r"^(chapter|unit|lesson|module|part)\s+([\dIVXivx]+|[a-z]+)\b[\s:.\-–]*(.*)$", re.I)
TOPIC_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+([^\d].{1,90})$")
SUBTOPIC_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{1,2})\s+(.{2,90})$")
MD_RE = re.compile(r"^(#{1,4})\s+(.+)$")
GRADE_RE = re.compile(r"\b(?:grade|class|std\.?|standard|year)\s*[-:]?\s*(\d{1,2}|[ivx]{1,4})\b", re.I)
SUBJECTS = ["mathematics", "maths", "math", "science", "physics", "chemistry", "biology", "english", "malayalam",
            "hindi", "tamil", "kannada", "social science", "social studies", "history", "geography", "civics",
            "computer science", "coding", "environmental studies", "evs", "economics", "art", "music"]

SECTION_KEYS = {
    "objectives": re.compile(r"^(learning\s+)?(objectives?|outcomes?|goals?)\b|^students\s+will\b|^by the end of", re.I),
    "activities": re.compile(r"^(activity|activities|try this|do it yourself|experiment|project)\b", re.I),
    "questions": re.compile(r"^(exercises?|questions?|review questions?|check your (understanding|progress)|let us (answer|recall)|quiz|assessment)\b", re.I),
    "definitions": re.compile(r"^(key (words|terms|concepts)|glossary|definitions?|vocabulary|new words)\b", re.I),
    "examples": re.compile(r"^(examples?|for example|e\.g\.)\b", re.I),
}
DEF_RE = re.compile(r"^(?:the\s+|an?\s+)?([A-Za-z][A-Za-z\- ]{1,40}?)\s+(?:is|are|means|refers to|is called|is defined as)\s+(.{8,300}?[.!])", re.I)
COLON_DEF_RE = re.compile(r"^([A-Za-z][A-Za-z\- ]{1,40})\s*[:–\-]\s+(.{8,300})$")
ANSWER_RE = re.compile(r"^(answer|ans|a)\s*[:.)\-]\s*(.+)$", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-•*▪●]|\d{1,2}[.)]|[a-z][.)]|\([a-z0-9]\))\s+")
STOP_TERMS = {"this", "it", "that", "there", "these", "they", "he", "she", "we", "you", "i", "which", "what"}


def clean_line(line):
    return re.sub(r"\s+", " ", line).strip()


def _strip_bullet(line):
    return BULLET_RE.sub("", line).strip()


def detect_meta(pages, filename=""):
    head = " ".join(p["text"][:1500] for p in pages[:2]) + " " + filename.replace("_", " ")
    grade = ""
    m = GRADE_RE.search(head)
    if m:
        grade = m.group(1).upper() if not m.group(1).isdigit() else m.group(1)
    low = head.lower()
    subject = next((s for s in SUBJECTS if re.search(rf"\b{re.escape(s)}\b", low)), "")
    subject = {"maths": "mathematics", "math": "mathematics", "evs": "environmental studies"}.get(subject, subject)
    return {"grade": grade, "subject": subject.title()}


def heading_level(line):
    """Return (level, title) or None. level: chapter | topic | subtopic."""
    m = MD_RE.match(line)
    if m:
        n = len(m.group(1))
        title = m.group(2).strip()
        if any(k.match(title) for k in SECTION_KEYS.values()) and len(title.split()) <= 5:
            return None  # "Exercises", "Learning Objectives"… are sections, not topics
        cm = CHAPTER_RE.match(title)
        if n == 1 or cm:
            return "chapter", title
        return ("topic" if n == 2 else "subtopic"), re.sub(r"^\d+(\.\d+)+\s+", "", title)
    m = CHAPTER_RE.match(line)
    if m and len(line) < 120:
        return "chapter", line
    m = SUBTOPIC_RE.match(line)
    if m and not line.endswith("."):
        return "subtopic", m.group(4).strip()
    m = TOPIC_RE.match(line)
    if m and not line.endswith(".") and len(line.split()) <= 12:
        return "topic", m.group(3).strip()
    letters = [c for c in line if c.isalpha()]
    if 2 <= len(line.split()) <= 8 and len(line) < 70 and letters and all(c.isupper() for c in letters if c.isascii()) \
            and sum(c.isascii() for c in letters) > len(letters) * 0.8 and not any(k.match(line) for k in SECTION_KEYS.values()):
        return "topic", line.title()
    return None


def merge_wrapped(text):
    """Join lines that a PDF wrapped mid-sentence, so sentences can be parsed."""
    out = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if (out and out[-1] and line and len(out[-1]) >= 60 and not re.search(r"[.?!:;]$", out[-1])
                and not BULLET_RE.match(line) and not MD_RE.match(line) and not heading_level(line)
                and not ANSWER_RE.match(line)):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return out


def _new_section(level, title, page):
    return {"level": level, "title": title[:290], "page": page, "lines": [], "objectives": [], "definitions": [],
            "examples": [], "activities": [], "questions": [], "children": []}


def structure(pages, title_hint=""):
    """Return {"meta": {...}, "chapters": [section…]} — sections nest chapter → topic → subtopic."""
    meta = detect_meta(pages, title_hint)
    chapters, chapter, topic, current = [], None, None, None
    mode, last_q = None, None

    def ensure_chapter(page):
        nonlocal chapter
        if chapter is None:
            chapter = _new_section("chapter", title_hint or "Chapter 1", page)
            chapters.append(chapter)
        return chapter

    for p in pages:
        page = p["page"]
        for line in merge_wrapped(p["text"]):
            if not line:
                continue
            h = heading_level(line)
            if h:
                level, title = h
                mode = None
                if level == "chapter":
                    chapter = _new_section("chapter", title, page)
                    chapters.append(chapter)
                    topic = None
                    current = chapter
                elif level == "topic":
                    topic = _new_section("topic", title, page)
                    ensure_chapter(page)["children"].append(topic)
                    current = topic
                else:
                    sub = _new_section("subtopic", title, page)
                    (topic or ensure_chapter(page))["children"].append(sub)
                    current = sub
                continue
            sec = current or ensure_chapter(page)
            text = _strip_bullet(line.lstrip("#").strip())
            key = next((k for k, rx in SECTION_KEYS.items() if rx.match(text)), None)
            if key and len(text.split()) <= 8 and not text.endswith("?"):
                mode = key
                rest = re.split(r"[:\-–]", text, maxsplit=1)
                text = rest[1].strip() if len(rest) > 1 and key != "examples" else ""
                if not text:
                    continue
            src = {"page": page}
            sec["lines"].append((page, line))
            am = ANSWER_RE.match(text)
            if am and last_q is not None:
                last_q["answer"] = am.group(2).strip()
                continue
            if text.endswith("?") and len(text) > 8:
                last_q = {"question": text, "answer": "", **src}
                sec["questions"].append(last_q)
                continue
            if mode == "objectives":
                sec["objectives"].append(text)
            elif mode == "activities":
                sec["activities"].append({"text": text, **src})
            elif mode == "examples" or re.match(r"^(for example|example|e\.g\.)", text, re.I):
                sec["examples"].append({"text": re.sub(r"^(for example|example\s*\d*|e\.g\.)\s*[:,.\-]?\s*", "", text, flags=re.I) or text, **src})
            elif mode == "definitions":
                m = COLON_DEF_RE.match(text) or DEF_RE.match(text)
                if m:
                    sec["definitions"].append({"term": m.group(1).strip(), "meaning": m.group(2).strip(), **src})
            else:
                for sent in re.split(r"(?<=[.!])\s+", text):
                    m = DEF_RE.match(sent)
                    if m and m.group(1).split()[0].lower() not in STOP_TERMS and len(m.group(1).split()) <= 4:
                        term = m.group(1).strip()
                        if not any(d["term"].lower() == term.lower() for d in sec["definitions"]):
                            sec["definitions"].append({"term": term, "meaning": sent.strip(), **src})
    return {"meta": meta, "chapters": chapters}


def section_text(sec):
    return "\n".join(l for _, l in sec["lines"])


def chunk_section(sec, words=120, overlap=30):
    """Split a section into overlapping chunks, keeping the page of each chunk's first line."""
    out, buf, buf_page = [], [], None
    for page, line in sec["lines"]:
        if buf_page is None:
            buf_page = page
        buf.extend(line.split())
        while len(buf) >= words:
            out.append({"page": buf_page, "text": " ".join(buf[:words])})
            buf = buf[words - overlap:]
            buf_page = page
    if buf:
        out.append({"page": buf_page or sec["page"], "text": " ".join(buf)})
    if out:
        out[0]["text"] = f"{sec['title']}: {out[0]['text']}"
    return out
