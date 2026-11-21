# Mabix AI — Architecture

## 1. What existed before (analysis of the original `app.py`)

| Area | Original implementation |
|---|---|
| Stack | Single 2,100-line Flask file, inline HTML/JS UI (`render_template_string`), no database, no auth |
| Script writing | Ollama `llama3` (`generate_script`, `detect_scenes`, `generate_title`, `generate_description`) |
| News source | Google News RSS via `feedparser` (`/news`, 24 categories) |
| Visuals | Pexels → Pixabay stock images, fallback to Stable Diffusion 1.5 (`SG161222/Realistic_Vision_V5.1_noVAE`) on CUDA |
| Motion | AnimateDiff (`guoyww/animatediff-motion-adapter-v1-5-2`) for the "content" mode, 16 frames @ 8 fps |
| Voice | gTTS, one `voice.mp3` per video |
| Render | moviepy 2: Ken Burns stills + burned-in captions (`create_video`), or clip concatenation + procedural ambience (`create_video_from_clips`) |
| Jobs | One global `progress_status` dict + `threading.Thread` per request |
| Output | `videos/<YYYY-MM-DD>/<title>.mp4` |
| Publishing | `/upload_youtube` — YouTube Data API v3 with `youtube_token.json` OAuth token |

**Findings that shaped the plan**

* **There was no direct (local file) upload feature.** The only "upload" was publishing a *generated* video to YouTube. Local upload was therefore built new; publishing reuses the original YouTube code.
* `/generate-story` was written with FastAPI signatures (`UploadFile`, `File(...)`) on a Flask app and could not work. `/api/chat/start` referenced undefined `db`/`ChatSession`. Both were broken; the story endpoint now has a working Flask equivalent; the chat route was dropped.
* Pexels and Pixabay API keys were **hardcoded** in source (now env vars — the old keys are in git history and should be rotated).
* `/video?path=` served any file on disk and `/open_folder` opened any path (now restricted to media folders and login-protected).
* `generate_video_with_reference` passed `image=` to `AnimateDiffPipeline`, which does not accept it, so it always fell back to a black clip. Real image-to-video is now provided by Stable Video Diffusion (opt-in).

## 2. New structure

```
app.py                      thin entry point (also re-exports the original pipeline functions)
mabix/
  __init__.py               create_app(): config, DB migration, blueprints, worker start
  config.py                 env-driven config (+ minimal .env loader)
  db.py                     SQLite (stdlib) + versioned migrations (PRAGMA user_version)
  repo.py                   videos / jobs / settings data access, dashboard queries
  auth.py                   session login, CSRF, login throttling, password change
  jobs.py                   persistent job queue, single GPU worker, handlers
  storage.py                media folders and path-safety
  media.py                  probe (duration/resolution) + thumbnails
  capabilities.py           detects which AI dependencies really work (shown in UI)
  pipelines/
    legacy.py               ORIGINAL news/content pipeline, bodies unchanged
    models.py               GPU model manager: SD, AnimateDiff, Wan 2.1, SVD (one resident at a time)
    llm.py                  Ollama HTTP client (+ JSON extraction)
    studio.py               module specs, planning prompts, per-scene AI video generation
    render.py               shared compositor: narration, captions, highlights, transitions, music
  publishing/youtube.py     the original YouTube upload, reusable
  routes/pages.py           HTML pages
  routes/api.py             JSON API + media serving
  routes/legacy.py          original endpoints kept (/generate, /progress, /upload_youtube, …)
  templates/, static/       UI (Jinja + vanilla JS, no build step)
tests/test_app.py           end-to-end tests (models mocked, everything else real)
```

## 3. Generation pipeline (shared by all AI modules)

```
prompt ─▶ script job (Ollama) ─▶ REVIEW (edit scenes) ─▶ [storyboard job: SD keyframes]
       ─▶ render job: per scene AI video (Wan 2.1 / AnimateDiff, or SVD from keyframe)
                      → gTTS narration per scene → captions + keyword highlights + headings
                      → transitions → music bed → MP4 → thumbnail → Video Library
```

* Per-scene narration gives exact caption/scene sync (the original split one voice track evenly).
* A scene's AI clip is reused on re-render unless its visual prompt changed; "Regenerate" rebuilds all.
* If an AI clip fails (e.g. OOM) the scene falls back to an AI keyframe with camera motion, and this is
  logged on the job and shown per scene ("ai-image (camera motion)") — never silently.
* If the LLM / GPU / models are missing, the job **fails with the reason**; nothing is simulated.

News has two modes: **Quick** runs the original `generate_news_short()` unchanged; **Review** reuses the
original news prompts (`generate_title/script/detect_scenes/description`) and stock-image search, then the
shared review + render flow.

## 4. Jobs

`pending` (waits for a parent job) → `queued` → `processing` → `completed` | `failed` | `cancelled`.
One worker thread processes jobs sequentially (one GPU). Progress, stage text and logs are stored in
SQLite and polled by the UI. Duplicate active jobs are rejected (409). Cancellation is immediate for
queued jobs and cooperative (between scenes/steps) for running ones. Jobs interrupted by a restart are
marked failed and can be retried.

## 5. RTX 5080 notes

* Needs a CUDA 12.8+ PyTorch build (sm_120). The readiness panel flags builds without sm_120 kernels.
* `models.py` keeps only one diffusion pipeline resident and frees VRAM before loading another.
* Wan 2.1 T2V 1.3B (bf16) runs in roughly 8–10 GB; SVD uses CPU offload to stay under 16 GB.

## 6. Not available (needs additional models)

* **Lip-sync** — requires e.g. Wav2Lip/SadTalker; not integrated, shown as unavailable.
* **Premium voices** — narration uses gTTS (internet required). A local TTS (e.g. Piper/XTTS) would be a drop-in for `render.synthesize`.
* **Other platforms** (Instagram/Facebook publishing) — only YouTube existed; others need their APIs and app credentials.
