# Speech: STT and TTS — design, evaluation and benchmark plan

**What is measured vs. what is from documentation.** None of the STT/TTS *models* could be downloaded in
the build environment (Hugging Face was blocked), so no model accuracy or latency numbers here come from
our own runs yet. Everything else — audio conversion, VAD, segmentation, routing, code-switching
analysis, streaming, barge-in, the benchmark tool — was tested with real (espeak-ng) speech audio.
Fill in the tables below by running the benchmark on the RTX 5080 machine with real recordings.

## Pipeline

```
mic (ESP32 / browser, any rate/channels/format)
  → explicit conversion: downmix → resample (polyphase) → 16 kHz mono float32
  → VAD (WebRTC | Silero | energy) on 30 ms frames
  → segmentation: start after 90 ms of speech, 300 ms pre-roll, end after MABCAT_EOU_MS (800) of silence,
                  drop < 250 ms (coughs/taps), force-split at 30 s
  → per-utterance noise handling (80 Hz high-pass + peak normalise; optional gentle spectral gate ≤ 60 %)
  → language decision (session hint → audio LID → profile)
  → router → provider → transcript (original language, never translated)
  → code-switching label (per-word script) → AI teacher
```

Partial transcripts: while speech continues, the growing utterance is re-transcribed every
`MABCAT_STT_PARTIAL_INTERVAL_MS` (900 ms) with the same provider; stale partials are dropped; the final is
always produced from the complete utterance. On a GPU this is affordable for short child questions; set the
interval higher or 0-equivalent (very large) on CPU-only servers.

## STT providers

| | AI4Bharat IndicConformer 600M | faster-whisper (large-v3 / turbo) | OpenAI transcription |
|---|---|---|---|
| Languages | 22 scheduled Indian languages (as, bn, brx, doi, gu, hi, kn, kok, ks, mai, **ml**, mni, mr, ne, or, pa, sa, sat, sd, **ta**, te, ur) — **no English** | ~99 incl. en, ml, hi, ta, kn, te | many incl. en, hi, ml, ta |
| Language ID | **No** — must be told the language | Yes (`detect_language`) | Optional `language` hint |
| Mixed ml+en | English words come out transliterated in Malayalam script | Often writes English words in Latin; may drift into translation on short clips — benchmark | Unknown for Malayalam code-switching — benchmark |
| Streaming | No (we emulate partials) | No (we emulate partials) | `stream=true` deltas on gpt-4o(-mini)-transcribe; Realtime API for true streaming |
| Word timestamps | No | Yes | No (json format) |
| Confidence | None exposed → `null` | avg_logprob (not calibrated) → `null`, raw value in `details` | None → `null` |
| Runs on | GPU (PyTorch) or CPU | GPU (CTranslate2 float16) or CPU (int8) | Cloud — child audio leaves your server |
| Licence | MIT on the model card (verify) | MIT (code), Whisper weights MIT | Commercial API, per-minute price |
| Integration | `transformers` + `trust_remote_code` | `pip install faster-whisper` | REST `/v1/audio/transcriptions` |

RTX 5080 notes: PyTorch needs a CUDA 12.8+ (cu128) build for sm_120. CTranslate2 (faster-whisper) also
needs a CUDA-12 build that supports Blackwell; if `device=cuda` fails the provider falls back to CPU int8
and reports `device: cpu` in every transcript's details.

### Default routes (starting point — replace with benchmark winners)

`en → faster-whisper` · `ml, hi, ta, kn, te, ml-en, hi-en → indicconformer` · `* → faster-whisper`.
Cloud is used only when `MABCAT_STT_CLOUD_FALLBACK=1` (or explicitly requested), and every transcript
records `provider`, `model`, `routing.reason` and `routing.fallback_from`.

Set with `MABCAT_STT_ROUTES="ml-en:faster-whisper,…"` or a JSON file `MABCAT_STT_ROUTES_FILE`.

### Benchmark (fill this in)

```bash
# 1. record real samples (with parental consent for children) — 16 kHz mono WAV is ideal
# 2. write samples/manifest.jsonl:  {"audio":"ml_001.wav","text":"photosynthesis എന്താണ്","language":"ml","tags":["mixed","child","noisy"]}
python -m mabixai.backend.speech.benchmark samples/manifest.jsonl --providers faster-whisper,indicconformer,openai
# report: storage/mabcat/benchmarks/latest.md (also GET /api/v1/stt/benchmark)
```

Recommended set (≥ 30 clips per row): English child questions · Malayalam · Malayalam+English mixed ·
Hindi · Hindi+English · Tamil · Kannada · each with quiet and classroom-noise variants, several speakers and
accents. Metrics produced: WER, CER (better for Malayalam's agglutinative words), mean latency, RTF,
CPU-seconds per audio second, GPU peak memory, failure rate, USD per audio hour.

| Group | IndicConformer WER/CER | faster-whisper WER/CER | OpenAI WER/CER | Chosen |
|---|---|---|---|---|
| en | n/a | — | — | — |
| ml | — | — | — | — |
| ml-en mixed | — | — | — | — |
| hi | — | — | — | — |
| ta / kn | — | — | — | — |

### Code-switching

`code_switching.analyse()` tags every word by Unicode script and labels the transcript, e.g.
`"ഈ chapter എനിക്ക് explain ചെയ്യാമോ?" → ml-en` (primary Malayalam, mixed with English) and
`"What is മഴ in English?" → en-ml`. Verified on all examples from the specification (unit tests).
Limitation: romanised Malayalam/Hindi in Latin letters ("enthanu") is tagged English — script cannot tell.

## VAD and noise

| Engine | Tested here | Notes |
|---|---|---|
| WebRTC VAD (default) | ✅ split two espeak utterances (2.44 s / 2.25 s spoken → 2.67 s / 2.43 s segments incl. pre-roll) with background hiss | CPU, ~µs per frame |
| Energy (fallback) | ✅ same test after fixing its noise-floor tracker (rolling 10th percentile) | weakest in real noise |
| Silero VAD | ❌ not tested (needs torch) | better in noise; recommended on the GPU server: `pip install silero-vad`, `MABCAT_VAD=silero` |

Noise handling defaults to an 80 Hz high-pass + normalisation only. Aggressive denoisers smear consonants
and are known to hurt recognition of children's speech, so spectral gating is opt-in and capped at 60 %.

## TTS engines

| | Kokoro-82M | Piper | espeak-ng | gTTS |
|---|---|---|---|---|
| Quality | Natural, expressive | Good, slightly flat | Robotic | Good |
| Languages | en-US, en-GB, hi, es, fr, it, ja, pt-BR, zh — **no ml/ta/kn** | Per-voice ONNX models (en, hi … check the voice list for ml/ta/kn) | ~100 incl. ml, ta, kn, hi | many incl. ml |
| Output | 24 kHz | 16–22.05 kHz | 22.05 kHz | MP3 |
| Hardware | GPU fast; CPU usable (82M params) | CPU real-time even on a Raspberry Pi | CPU | Cloud |
| Streaming | per sentence (we chunk by sentence) | per sentence | per sentence | per sentence |
| Licence | Apache-2.0 weights | **piper-tts ≥ 1.3 is GPL-3.0** (1.2.0 MIT); each voice has its own licence | GPL-3.0 (used as separate process) | Google ToS; audio leaves the server |
| Tested here | ❌ build fails on Python 3.13 (spaCy) — use Python 3.10–3.12 | ⚠ package installs, no voice files downloadable | ✅ used for all streaming tests | — |

The TTS router picks, **per sentence**, the first enabled engine that supports that sentence's language,
so "Photosynthesis is… നമുക്ക് ഒരു simple example നോക്കാം!" is spoken by an English voice and then a
Malayalam voice (verified with espeak-ng). Recommended order on the RTX 5080: `kokoro,piper,espeak`
with Piper Malayalam/Tamil/Kannada voices configured via `MABCAT_PIPER_MODELS`.

Measured with espeak-ng over a real WebSocket: first TTS audio frame ~40 ms after the reply was ready
(1.1 s for the very first, cold call). Kokoro/Piper latency must be measured on the target machine:
`curl -F text="…" -F engine=kokoro http://host/api/v1/tts/synthesize -o out.wav` with timing.
