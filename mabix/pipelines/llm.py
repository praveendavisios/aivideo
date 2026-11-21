"""Ollama access (same local llama3 model the original app used)."""
import json
import logging

import requests

from ..config import Config
from .models import model_setting

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def ollama_host():
    return model_setting("ollama_host", Config.OLLAMA_HOST).rstrip("/")


def ollama_model():
    return model_setting("ollama", Config.OLLAMA_MODEL)


def ollama_chat(messages, model=None, timeout=600, json_mode=False):
    """Return a response shaped like ollama.chat(): {"message": {"content": ...}}."""
    body = {"model": model or ollama_model(), "messages": messages, "stream": False}
    if json_mode:
        body["format"] = "json"
    try:
        r = requests.post(f"{ollama_host()}/api/chat", json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise LLMError(f"Cannot reach Ollama at {ollama_host()} — is `ollama serve` running? ({exc})") from exc
    if r.status_code != 200:
        raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
    return r.json()


def chat_text(prompt, **kw):
    return ollama_chat([{"role": "user", "content": prompt}], **kw)["message"]["content"].strip()


def extract_json(text):
    """Parse the first JSON object/array in an LLM response."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise LLMError("The language model did not return valid JSON. Try regenerating.")


def chat_json(prompt, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            return extract_json(chat_text(prompt, json_mode=True))
        except LLMError as exc:
            last = exc
            if "Cannot reach" in str(exc) or "Ollama error" in str(exc):
                raise
            log.warning("JSON parse failed (attempt %s): %s", attempt + 1, exc)
    raise last


def is_available(timeout=2):
    try:
        r = requests.get(f"{ollama_host()}/api/tags", timeout=timeout)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        names = [m.get("name", "") for m in r.json().get("models", [])]
        want = ollama_model()
        if not any(n == want or n.split(":")[0] == want for n in names):
            return False, f"model '{want}' not pulled (ollama pull {want})"
        return True, want
    except requests.RequestException:
        return False, f"not reachable at {ollama_host()}"
