# Test report — MabixAI RoboCat backend (first iteration)

Build environment: cloud Linux container, Python 3.13, **no GPU, no Ollama, Hugging Face blocked**,
no Docker daemon. Real speech for tests was synthesised with espeak-ng (en, ml, hi, ta, kn).

## Automated tests — `pytest mabixai/tests tests` → **47 passed**

24 RoboCat integration tests + the 23 existing video-studio tests (studio unchanged and still mounted).

| Area | What ran for real | Result |
|---|---|---|
| Auth & roles | login, bearer tokens, admin-only user creation, 401/403 | ✅ |
| Student isolation | other parent → 404; teacher sees only class members, read-only; consent only by parent | ✅ |
| Devices | token shown once, stored hashed, auth by first message, wrong token rejected | ✅ |
| Curriculum extraction | **real** PDF (text), **scanned PDF (Tesseract OCR)**, DOCX (heading styles), PPTX (slides) | ✅ same chapter→topic tree from every format, subject "Science" and grade "5" detected |
| Structuring | objectives, definitions, examples, activities, Q&A pairs with page refs | ✅ |
| RAG | BM25 hits carry filename + page; OCR'd text is searchable; unrelated query returns nothing | ✅ |
| Upload validation | wrong extension 415, spoofed content 422, size limit | ✅ |
| Lesson plans | offline plan from document content; teacher edit + approve; robot teaches the approved version | ✅ |
| Teaching loop (WebSocket) | consent gate → session → lesson intro → question → wrong answer (hint, answer not revealed) → question instead of answer (helped, re-asked) → correct → "I don't know" ×2 (gentle reveal) → barge-in answer → completion 2/3 → summary → progress saved | ✅ |
| Grounded chat | answer cites the document; off-curriculum question → "couldn't find that", `in_curriculum:false` | ✅ |
| LLM mode | with a faked LLM: `[S#]` excerpts in the prompt, sources mapped back, language "Malayalam mixed with English" passed | ✅ (real Ollama not tested) |
| TTS streaming | espeak-ng → per-sentence PCM → binary `MC` frames → `tts_audio_start/end`, interruption | ✅ |
| Audio socket | **48 kHz stereo** PCM in binary frames → downmix/resample → WebRTC VAD → 2 utterances; duplicate frame dropped; finals forwarded to the teacher; base64 JSON chunks; voice-consent gate | ✅ STT *model* replaced by a test provider |
| Router | ml→IndicConformer, en→faster-whisper, auto never picks IndicConformer, cloud only when enabled, fallback recorded, clear error when nothing works | ✅ |
| Code-switching | all examples from the spec (ml-en, en-ml, hi, ta, kn) | ✅ |
| Benchmark tool | WER/CER maths, run on a synthetic manifest, JSON + Markdown report | ✅ |
| Usage limits, robot commands, heartbeat, erasure + audit | | ✅ |
| Video material | creates a Short-Answer job in the existing studio queue | ✅ (render needs the GPU) |

## Live tests (uvicorn on TCP)

* External Python `websockets` client (behaves like the firmware): full lesson over the network,
  2/3 correct, summary returned; TTS first audio frame ≈ 40 ms after the reply (1.1 s cold first call, espeak).
* `device_simulator.py`: greeting + grounded answer received, TTS saved to WAV.
* Real-time speech stream (100 ms frames, real-time pacing): speech detected at 0.18 s, utterance 2.64 s,
  language routed; transcription failed **as expected** with the honest error
  "Whisper model could not be loaded: 403 Forbidden" (Hugging Face blocked here).
* Browser (Chromium, Playwright): portal (teacher upload view, tree, plan generation, flashcards, RAG search;
  parent progress), teaching console (connect, chat, lesson, quiz answer, robot expression), **browser
  microphone** via a fake audio device → server VAD detected speech; speech console compare (WER/CER).
  No page errors.

## Not tested / incomplete

| Item | Status | Needs |
|---|---|---|
| faster-whisper, IndicConformer, OpenAI STT accuracy & latency | code complete, **never run against a model** | GPU PC + real recordings → run the benchmark |
| Malayalam-English mixed **speech** recognition quality | text-level code-switching verified only | benchmark with real children's recordings (with consent) |
| Kokoro TTS | does not build on Python 3.13 | Python 3.12 venv on the GPU PC |
| Piper TTS | package installs; no voice files | download voices; note GPL-3.0 for piper-tts ≥ 1.3 |
| Silero VAD, spectral noise reduction | optional, not installed | `pip install silero-vad noisereduce` |
| Real LLM (Ollama/OpenAI/Gemini) teaching quality | code paths tested with a fake | Ollama on the GPU PC |
| Illustration / video generation for lessons | wired to the studio GPU pipeline, not run | GPU |
| PostgreSQL | SQLAlchemy models portable, not run | run tests with `DATABASE_URL=postgresql+psycopg://…` |
| Docker image | written, not built | Docker + NVIDIA Container Toolkit |
| ESP32-S3 firmware | protocol, hardware notes and reference simulator only | Phase 6 |
| Transcript retention purge job | setting exists, job not implemented | next iteration |
| Multi-worker deployment | per-process WebSocket hub | Redis pub/sub before `--workers > 1` |
| Rate limiting beyond login | not implemented | reverse proxy limits for now |

**Not production-ready**: the speech models, LLM teaching quality, firmware and Docker image have not
been verified end to end. The backend, protocol, curriculum pipeline and consoles are ready for testing on
the GPU machine.
