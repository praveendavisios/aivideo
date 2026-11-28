# MabixAI RoboCat — architecture

The RoboCat is the child's interface; the backend is the teacher. One FastAPI process serves the RoboCat
API and WebSockets and mounts the existing Mabix AI video studio (Flask) unchanged, so curriculum videos
are produced by the same studio pipeline, queue and library.

```
                 ┌────────────── FastAPI (uvicorn) ──────────────────────────────────────────┐
 ESP32 robot ──▶ │ /ws/mabcat/{id}        teaching socket ─┐                                  │
 web console ──▶ │ /ws/mabcat/audio/{id}  STT socket ──────┤  DeviceHub (per-device conns)   │
 portal      ──▶ │ /api/v1/*  REST (auth, students, devices, curricula, plans, materials, …)  │
                 │ /console/* testing consoles          /  (mounted) Mabix video studio       │
                 └─────────────────────────────────────────────────────────────────────────────┘
   teaching/teacher.py ── TeacherEngine: lesson flow, evaluation, re-teaching, grounded chat
   teaching/student_engine.py ── mastery, level, next lesson, revision, usage limits
   teaching/llm.py ── Ollama | OpenAI | Gemini (keys server-side) ; offline mode when none
   curriculum/ extraction (pdf/docx/pptx/txt/OCR) → structuring → chunks → rag (BM25 [+ embeddings])
              lesson_planner (LLM-grounded with [S#] citations | offline from extracted content)
              materials (flashcards, worksheet, quiz, illustration → studio GPU models, video → studio jobs)
   speech/    audio/ preprocessing · vad · noise_reduction · segmentation · recorder
              providers/ indicconformer · faster_whisper · cloud_stt   router · session_manager
              language/ detection · code_switching          tts/engines (kokoro, piper, espeak, gtts)
   db: SQLAlchemy (SQLite default, PostgreSQL via DATABASE_URL) — see models.py
```

## Package layout (`mabixai/`)

```
backend/
  main.py                FastAPI app, health, startup (audio purge, warm-up), studio mount
  config.py db.py models.py security.py
  api/  accounts.py curriculum.py teaching.py stt.py audio.py websocket.py protocol.py
  curriculum/  extraction.py structuring.py rag.py service.py lesson_planner.py materials.py
  teaching/    llm.py personas.py teacher.py student_engine.py
  speech/      router.py session_manager.py benchmark.py
               providers/ base.py indicconformer.py faster_whisper.py cloud_stt.py
               audio/ preprocessing.py vad.py noise_reduction.py segmentation.py recorder.py
               language/ detection.py code_switching.py
               tts/ engines.py
web-console/   index.html (teaching + robot simulator) · stt.html · portal.html
esp32-firmware/ device_simulator.py (reference client) · README.md
tests/         integration tests + real document fixtures
```

## Data model

`User`(admin|teacher|parent) · `ApiToken`(hashed) · `Student`(parent-owned; nickname, grade, language,
consent_learning, consent_audio, daily limit, level) · `ClassGroup`/`ClassMember` (teacher sees only joined
students) · `Device`(hashed token, linked student) · `Curriculum` → `Document` → `Chunk`(text, page,
node, optional embedding) · `CurriculumNode` tree subject → grade → chapter → topic → subtopic with
objectives, definitions, examples, activities, questions, prerequisites, sources · `LessonPlan`
(draft|approved, generator) · `Material` · `Assignment` · `TeachingSession`(state, active_seconds) ·
`Turn`(transcript incl. STT provider) · `Attempt`(graded answers) · `Progress`(mastery per node) ·
`AuditLog` · `UsageCost`.

## Key decisions

* **RAG, not fine-tuning**, per curriculum: every answer and plan carries document/page sources; when
  retrieval finds nothing the teacher says so (`in_curriculum: false`) instead of inventing syllabus content.
* **Offline mode is real, not a mock**: without an LLM the teacher still runs the full loop using only what
  extraction found (book questions/answers, definitions, examples) and grades by key-word overlap. Every
  response says which engine produced it.
* **Approved plans win**: teachers review/edit generated plans; the robot teaches from the approved one.
* **Transport-independent engines**: the teacher and audio session return protocol messages; WebSocket
  and REST layers only deliver them, which keeps them unit-testable.
* **One GPU worker** for heavy video jobs (studio) — illustrations share the studio's model manager lock.
* **Privacy by default**: raw child audio is not stored (opt-in temporary storage with retention), consent
  gates for tutoring and voice, minimal profile data (nickname, grade, age), erasure endpoint, audit log.

## Security

Passwords: werkzeug salted hashes. API: bearer tokens (random, stored as SHA-256, 12 h expiry). Devices:
per-device tokens (shown once, stored hashed, rotatable, disable switch). Role-based access with student
isolation (parents: own children; teachers: children enrolled in their classes, read-only; admin: all).
Uploads: extension allow-list, content signature check, size limits, files stored outside the web root.
WebSocket auth by first message (no tokens in URLs). Keys (OpenAI, Gemini, Pexels…) only in server env.
TLS via reverse proxy (Caddy/nginx) — see DEPLOYMENT.md.
