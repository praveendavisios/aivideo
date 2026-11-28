"""Retrieval over curriculum chunks.

BM25 lexical retrieval always works (Unicode-aware, so Malayalam/Hindi text is
indexed too). If MABCAT_EMBEDDINGS is set (e.g. "ollama:nomic-embed-text" or
"openai:text-embedding-3-small") chunks are also embedded and results are a
hybrid of both scores. Every result carries its source document and page.
"""
import logging
import math
import os
import re
import threading
from collections import Counter

from sqlalchemy import select

from ..models import Chunk, Document

log = logging.getLogger("mabcat.rag")
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STOP = set("a an the is are was were be to of and or in on at for with by from as it this that these those what "
           "how why when which who whom can could will would should do does did i you we they he she me my your our "
           "their its about into than then there here please tell explain mab cat".split())
EMBEDDINGS = os.environ.get("MABCAT_EMBEDDINGS", "")  # "", "ollama:<model>", "openai:<model>"


def tokenize(text):
    toks = [t.casefold() for t in TOKEN_RE.findall(text or "")]
    out = []
    for t in toks:
        if t in STOP or len(t) < 2 and t.isascii():
            continue
        if t.isascii() and len(t) > 4:  # light English stemming
            for suf in ("ing", "ies", "es", "ed", "s"):
                if t.endswith(suf) and len(t) - len(suf) >= 3:
                    t = t[: -len(suf)] + ("y" if suf == "ies" else "")
                    break
        out.append(t)
    return out


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = [tokenize(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query):
        q = tokenize(query)
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / (self.avg or 1)))
            out.append(s)
        return out


_cache = {}
_lock = threading.Lock()


def _index(db, curriculum_ids):
    key = tuple(sorted(curriculum_ids))
    count = db.query(Chunk).filter(Chunk.curriculum_id.in_(key)).count()
    with _lock:
        hit = _cache.get(key)
        if hit and hit["count"] == count:
            return hit
    rows = db.execute(select(Chunk, Document.filename).join(Document, Document.id == Chunk.document_id)
                      .where(Chunk.curriculum_id.in_(key))).all()
    items = [{"id": c.id, "text": c.text, "page": c.page, "document_id": c.document_id, "filename": fn,
              "node_id": c.node_id, "curriculum_id": c.curriculum_id, "embedding": c.embedding} for c, fn in rows]
    entry = {"count": count, "items": items, "bm25": BM25([i["text"] for i in items])}
    with _lock:
        _cache[key] = entry
    return entry


def embed(texts):
    """Return embeddings or None when no embedding backend is configured/reachable."""
    if not EMBEDDINGS:
        return None
    kind, _, model = EMBEDDINGS.partition(":")
    import requests
    from ..config import settings
    try:
        if kind == "ollama":
            out = []
            for t in texts:
                r = requests.post(f"{settings.OLLAMA_HOST}/api/embeddings", json={"model": model, "prompt": t}, timeout=60)
                r.raise_for_status()
                out.append(r.json()["embedding"])
            return out
        if kind == "openai" and settings.OPENAI_API_KEY:
            r = requests.post("https://api.openai.com/v1/embeddings", timeout=60,
                              headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                              json={"model": model, "input": texts})
            r.raise_for_status()
            return [d["embedding"] for d in r.json()["data"]]
    except Exception as exc:
        log.warning("Embedding backend unavailable: %s", exc)
    return None


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


def search(db, curriculum_ids, query, k=5, node_id=None, min_score=0.3):
    """Top-k chunks for query; boosts chunks of the current node. Each hit includes its source."""
    if not curriculum_ids:
        return []
    idx = _index(db, curriculum_ids)
    if not idx["items"]:
        return []
    scores = idx["bm25"].scores(query)
    top = max(scores) or 1.0
    scores = [s / top for s in scores]
    if any(i["embedding"] for i in idx["items"]):
        qv = embed([query])
        if qv:
            for n, it in enumerate(idx["items"]):
                if it["embedding"]:
                    scores[n] = 0.5 * scores[n] + 0.5 * max(0.0, _cos(qv[0], it["embedding"]))
    if node_id:
        scores = [s * 1.25 if it["node_id"] == node_id and s > 0 else s for s, it in zip(scores, idx["items"])]
    ranked = sorted(zip(scores, idx["items"]), key=lambda x: -x[0])
    hits = []
    for s, it in ranked[:k]:
        if s < min_score:
            break
        hits.append({"score": round(s, 3), "text": it["text"], "chunk_id": it["id"], "node_id": it["node_id"],
                     "source": {"document_id": it["document_id"], "filename": it["filename"], "page": it["page"]}})
    return hits


def cite(hits):
    return [h["source"] | {"chunk_id": h["chunk_id"]} for h in hits]
