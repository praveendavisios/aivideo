"""STT benchmark: compare providers on real recordings.

  python -m mabixai.backend.speech.benchmark samples/manifest.jsonl [--providers faster-whisper,indicconformer]

Manifest: one JSON object per line
  {"audio": "ml_001.wav", "text": "photosynthesis എന്താണ്", "language": "ml", "tags": ["mixed", "child", "noisy"]}
Paths are relative to the manifest. Use real recordings: different accents, children's
voices (with parental consent) and realistic background noise. Do NOT choose a
primary provider from synthetic audio — `--make-synthetic DIR` only builds an
espeak-ng set to smoke-test the pipeline itself.

Metrics per provider, overall and per language / tag:
  WER, CER, mean latency, real-time factor, failure rate, GPU peak memory,
  CPU seconds per audio second, estimated cost per audio hour (cloud).
Writes <DATA_DIR>/benchmarks/latest.json and latest.md.
"""
import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
import unicodedata

from ..config import settings
from .audio import noise_reduction, preprocessing as pp
from .router import REGISTRY, SpeechRouter

PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text):
    t = unicodedata.normalize("NFC", (text or "").casefold())
    return " ".join(PUNCT.sub(" ", t).split())


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref, hyp):
    r, h = normalise(ref).split(), normalise(hyp).split()
    return edit_distance(r, h) / len(r) if r else float(bool(h))


def cer(ref, hyp):
    r, h = normalise(ref).replace(" ", ""), normalise(hyp).replace(" ", "")
    return edit_distance(list(r), list(h)) / len(r) if r else float(bool(h))


def gpu_peak_mb():
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 2 ** 20, 1)
    except ImportError:
        pass
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            return float(out.split()[0])
        except Exception:
            return None
    return None


def load_manifest(path):
    base = os.path.dirname(os.path.abspath(path))
    items = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                it = json.loads(line)
                it["audio"] = os.path.join(base, it["audio"])
                it.setdefault("tags", [])
                items.append(it)
    return items


def _agg(rows):
    ok = [r for r in rows if not r.get("error")]
    n = len(rows)
    if not n:
        return {}
    dur = sum(r["duration"] for r in ok) or 0
    return {"samples": n, "failures": n - len(ok), "failure_rate": round((n - len(ok)) / n, 3),
            "wer": round(sum(r["wer"] for r in ok) / len(ok), 3) if ok else None,
            "cer": round(sum(r["cer"] for r in ok) / len(ok), 3) if ok else None,
            "mean_latency_s": round(sum(r["latency"] for r in ok) / len(ok), 3) if ok else None,
            "rtf": round(sum(r["latency"] for r in ok) / dur, 3) if dur else None,
            "cpu_s_per_audio_s": round(sum(r["cpu"] for r in ok) / dur, 3) if dur else None,
            "usd_per_audio_hour": round(sum(r.get("usd", 0) for r in ok) / dur * 3600, 3) if dur else None}


def run(manifest, provider_names=None, out_dir=None):
    items = load_manifest(manifest)
    router = SpeechRouter(providers=provider_names or list(REGISTRY), cloud_fallback=True)
    results = {"manifest": os.path.abspath(manifest), "samples": len(items), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "config": {"whisper_model": settings.WHISPER_MODEL, "indic_model": settings.INDIC_MODEL,
                          "indic_decoder": settings.INDIC_DECODER, "openai_model": settings.OPENAI_STT_MODEL,
                          "noise_reduction": settings.NOISE_REDUCTION}, "providers": {}}
    for name, prov in router.providers.items():
        ok, detail = prov.status()
        entry = {"status": detail, "rows": []}
        results["providers"][name] = entry
        if not ok:
            entry["skipped"] = detail
            continue
        try:
            prov.load()
        except Exception as exc:
            entry["skipped"] = f"load failed: {exc}"
            continue
        for it in items:
            x, sr = pp.decode_any(open(it["audio"], "rb").read())
            x = noise_reduction.clean(pp.to_standard(x, sr), 16000, settings.NOISE_REDUCTION)
            row = {"audio": os.path.basename(it["audio"]), "language": it.get("language"), "tags": it["tags"],
                   "reference": it["text"], "duration": round(len(x) / 16000, 2)}
            lang = (it.get("language") or "auto").split("-")[0]
            if not prov.supports(lang):
                row["error"] = f"language {lang} not supported"
                entry["rows"].append(row)
                continue
            cpu0 = resource.getrusage(resource.RUSAGE_SELF)
            t0 = time.perf_counter()
            try:
                t = prov.transcribe(x, language=None if lang == "auto" else lang)
                row.update(hypothesis=t.text, wer=round(wer(it["text"], t.text), 3), cer=round(cer(it["text"], t.text), 3),
                           detected_language=t.language, usd=t.details.get("usd", 0))
            except Exception as exc:
                row["error"] = f"{exc.__class__.__name__}: {exc}"[:300]
            cpu1 = resource.getrusage(resource.RUSAGE_SELF)
            row["latency"] = round(time.perf_counter() - t0, 3)
            row["cpu"] = round((cpu1.ru_utime - cpu0.ru_utime) + (cpu1.ru_stime - cpu0.ru_stime), 3)
            entry["rows"].append(row)
        rows = entry["rows"]
        entry["overall"] = _agg(rows)
        entry["by_language"] = {l: _agg([r for r in rows if r.get("language") == l]) for l in {r.get("language") for r in rows}}
        entry["by_tag"] = {t: _agg([r for r in rows if t in r["tags"]]) for t in {t for r in rows for t in r["tags"]}}
        entry["gpu_peak_mb"] = gpu_peak_mb()
    out_dir = out_dir or os.path.join(settings.DATA_DIR, "benchmarks")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "latest.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown(results))
    return results


def markdown(res):
    lines = [f"# STT benchmark — {res['started']}", "", f"Manifest: `{res['manifest']}` ({res['samples']} samples)", "",
             "| Provider | Samples | WER | CER | Latency (s) | RTF | CPU s/audio s | Fail rate | GPU MB | USD/audio h |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, e in res["providers"].items():
        if e.get("skipped"):
            lines.append(f"| {name} | — | skipped: {e['skipped']} | | | | | | | |")
            continue
        o = e["overall"]
        lines.append(f"| {name} | {o['samples']} | {o['wer']} | {o['cer']} | {o['mean_latency_s']} | {o['rtf']} | "
                     f"{o['cpu_s_per_audio_s']} | {o['failure_rate']} | {e.get('gpu_peak_mb')} | {o['usd_per_audio_hour']} |")
    for name, e in res["providers"].items():
        if e.get("skipped"):
            continue
        lines += ["", f"## {name} by language / tag", "", "| Group | Samples | WER | CER | RTF |", "|---|---|---|---|---|"]
        for g, o in sorted(e["by_language"].items(), key=lambda x: str(x[0])):
            lines.append(f"| lang={g} | {o['samples']} | {o['wer']} | {o['cer']} | {o['rtf']} |")
        for g, o in sorted(e["by_tag"].items()):
            lines.append(f"| tag={g} | {o['samples']} | {o['wer']} | {o['cer']} | {o['rtf']} |")
    lines += ["", "WER/CER are computed after case-folding and punctuation removal. Lower is better.",
              "Choose routes per language from these numbers, not from model popularity."]
    return "\n".join(lines) + "\n"


SYNTHETIC = [("en", "Mab Cat, what is photosynthesis?", ["clean"]), ("en", "What is multiplication?", ["clean"]),
             ("en", "Mab Cat, tell me a story.", ["clean"]), ("ml", "മഴ എങ്ങനെ ഉണ്ടാകുന്നു", ["clean"]),
             ("hi", "गुणा क्या होता है", ["clean"]), ("ta", "இது என்ன", ["clean"]), ("kn", "ಇದು ಏನು", ["clean"])]


def make_synthetic(out_dir):
    """espeak-ng clips for pipeline smoke tests only (robotic voice — not representative)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.jsonl"), "w", encoding="utf-8") as fh:
        for i, (lang, text, tags) in enumerate(SYNTHETIC):
            name = f"synthetic_{lang}_{i}.wav"
            subprocess.run(["espeak-ng", "-v", "en-us" if lang == "en" else lang, "-w", os.path.join(out_dir, name), text],
                           check=True)
            fh.write(json.dumps({"audio": name, "text": text, "language": lang, "tags": tags + ["synthetic"]},
                                ensure_ascii=False) + "\n")
    return os.path.join(out_dir, "manifest.jsonl")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?")
    ap.add_argument("--providers", default="")
    ap.add_argument("--make-synthetic", metavar="DIR")
    a = ap.parse_args(argv)
    if a.make_synthetic:
        print(make_synthetic(a.make_synthetic))
        return
    if not a.manifest:
        ap.error("manifest required")
    res = run(a.manifest, [p for p in a.providers.split(",") if p] or None)
    sys.stdout.write(markdown(res))


if __name__ == "__main__":
    main()
