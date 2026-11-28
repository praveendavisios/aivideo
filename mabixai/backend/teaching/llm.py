"""LLM reasoning provider: Ollama (local), OpenAI or Gemini. Keys stay on the server."""
import json
import logging
import time

import requests

from ..config import settings

log = logging.getLogger("mabcat.llm")


class LLMUnavailable(RuntimeError):
    pass


def provider():
    return settings.LLM_PROVIDER


def model_name():
    return {"ollama": settings.OLLAMA_MODEL, "openai": settings.OPENAI_MODEL,
            "gemini": settings.GEMINI_MODEL}.get(provider(), "none")


def _ollama(messages, json_mode):
    body = {"model": settings.OLLAMA_MODEL, "messages": messages, "stream": False,
            "options": {"temperature": 0.6}}
    if json_mode:
        body["format"] = "json"
    r = requests.post(f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat", json=body, timeout=settings.LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _openai(messages, json_mode):
    if not settings.OPENAI_API_KEY:
        raise LLMUnavailable("OPENAI_API_KEY not set")
    body = {"model": settings.OPENAI_MODEL, "messages": messages, "temperature": 0.6}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=settings.LLM_TIMEOUT,
                      headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(messages, json_mode):
    if not settings.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"]
    body = {"contents": contents, "generationConfig": {"temperature": 0.6}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
    r = requests.post(url, json=body, timeout=settings.LLM_TIMEOUT, headers={"x-goog-api-key": settings.GEMINI_API_KEY})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


BACKENDS = {"ollama": _ollama, "openai": _openai, "gemini": _gemini}


def chat(messages, json_mode=False, retries=1):
    fn = BACKENDS.get(provider())
    if not fn:
        raise LLMUnavailable("No LLM provider configured (MABCAT_LLM_PROVIDER)")
    last = None
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            out = fn(messages, json_mode)
            log.info("llm %s %.1fs", provider(), time.time() - t0)
            return out
        except LLMUnavailable:
            raise
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise LLMUnavailable(f"{provider()} unreachable: {last}")


def parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    for attempt in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            return json.loads(attempt)
        except ValueError:
            continue
    raise ValueError("LLM did not return valid JSON")


def chat_json(messages, retries=1):
    last = None
    for _ in range(retries + 1):
        try:
            return parse_json(chat(messages, json_mode=True))
        except ValueError as exc:
            last = exc
    raise LLMUnavailable(str(last))


_status = {"at": 0, "ok": None, "detail": ""}


def status():
    """Cheap availability check (cached 30 s)."""
    if time.time() - _status["at"] < 30 and _status["ok"] is not None:
        return _status
    ok, detail = False, ""
    p = provider()
    try:
        if p == "ollama":
            r = requests.get(f"{settings.OLLAMA_HOST.rstrip('/')}/api/tags", timeout=2)
            names = [m.get("name", "") for m in r.json().get("models", [])]
            ok = any(n.split(":")[0] == settings.OLLAMA_MODEL.split(":")[0] for n in names)
            detail = settings.OLLAMA_MODEL if ok else f"model {settings.OLLAMA_MODEL} not pulled"
        elif p == "openai":
            ok, detail = bool(settings.OPENAI_API_KEY), settings.OPENAI_MODEL if settings.OPENAI_API_KEY else "OPENAI_API_KEY missing"
        elif p == "gemini":
            ok, detail = bool(settings.GEMINI_API_KEY), settings.GEMINI_MODEL if settings.GEMINI_API_KEY else "GEMINI_API_KEY missing"
        else:
            detail = "no provider configured — offline teaching mode"
    except requests.RequestException:
        detail = f"Ollama not reachable at {settings.OLLAMA_HOST}"
    _status.update(at=time.time(), ok=ok, detail=detail, provider=p)
    return _status


def available():
    return bool(status()["ok"])
