"""Code-switching analysis of transcripts.

Children mix languages inside one sentence ("Photosynthesis എന്താണ്?"), so
language is analysed per word from its Unicode script rather than assuming
one language per recording. The original transcript is never modified.

Limitation: romanised Indian-language words written in Latin script
("enthanu") are tagged as English — script alone cannot tell them apart.
"""
import re

SCRIPTS = [  # (language code, first codepoint, last codepoint)
    ("ml", 0x0D00, 0x0D7F), ("hi", 0x0900, 0x097F), ("ta", 0x0B80, 0x0BFF), ("kn", 0x0C80, 0x0CFF),
    ("te", 0x0C00, 0x0C7F), ("bn", 0x0980, 0x09FF), ("gu", 0x0A80, 0x0AFF), ("pa", 0x0A00, 0x0A7F),
    ("or", 0x0B00, 0x0B7F), ("ur", 0x0600, 0x06FF),
]
NAMES = {"en": "English", "ml": "Malayalam", "hi": "Hindi", "ta": "Tamil", "kn": "Kannada", "te": "Telugu",
         "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "or": "Odia", "ur": "Urdu", "mr": "Marathi"}
WORD_RE = re.compile(r"[\wऀ-෿؀-ۿ]+", re.UNICODE)


def char_lang(ch):
    cp = ord(ch)
    for code, lo, hi in SCRIPTS:
        if lo <= cp <= hi:
            return code
    if ch.isascii() and ch.isalpha():
        return "en"
    return None


def word_lang(word):
    counts = {}
    for ch in word:
        lang = char_lang(ch)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else None


def analyse(text):
    """{"primary", "languages": {code: share}, "mixed", "segments": [{"lang", "text"}]}"""
    words = [(w, word_lang(w)) for w in WORD_RE.findall(text or "")]
    words = [(w, l) for w, l in words if l]
    if not words:
        return {"primary": "und", "languages": {}, "mixed": False, "segments": []}
    counts = {}
    for _, l in words:
        counts[l] = counts.get(l, 0) + 1
    total = sum(counts.values())
    shares = {l: round(c / total, 2) for l, c in sorted(counts.items(), key=lambda x: -x[1])}
    # An Indian script present usually marks the child's language (English words are loanwords),
    # unless English clearly dominates ("What is മഴ in English?").
    indic = [l for l in shares if l != "en"]
    primary = indic[0] if indic and shares.get("en", 0) < 0.7 else "en"
    segments = []
    for w, l in words:
        if segments and segments[-1]["lang"] == l:
            segments[-1]["text"] += " " + w
        else:
            segments.append({"lang": l, "text": w})
    return {"primary": primary, "languages": shares, "mixed": len(shares) > 1, "segments": segments}


def tag(primary, mixed_with=None):
    """Language label for a transcript, e.g. "ml", "ml-en"."""
    return f"{primary}-{mixed_with}" if mixed_with and mixed_with != primary else primary


def label(text):
    a = analyse(text)
    others = [l for l in a["languages"] if l != a["primary"]]
    return tag(a["primary"], others[0] if a["mixed"] and others else None)


def describe(lang):
    parts = (lang or "").split("-")
    names = [NAMES.get(p, p) for p in parts if p]
    return " mixed with ".join(names) if names else "unknown language"
