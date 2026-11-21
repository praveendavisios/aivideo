# Mabix AI

AI video creation & publishing studio: AI shorts, animations, stories, short-answer explainers,
news-feed videos, direct uploads, a central video library, job history and YouTube publishing.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it is built.

## Setup (Windows/Linux, RTX 50-series GPU)

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
cp .env.example .env                               # then edit it
ollama pull llama3 && ollama serve                 # script writer
python app.py                                      # http://localhost:5000
```

Sign in with `admin` / `admin` (development only). For production set `MABIX_ENV=production`,
`MABIX_SECRET_KEY` and `MABIX_ADMIN_PASSWORD_HASH` — the default credentials are refused there.

### AI video engines (Settings → AI models)

| Engine | Use | Notes |
|---|---|---|
| AnimateDiff (default) | text-to-video per scene | existing model, ~2 s clips |
| **Wan 2.1 T2V 1.3B** (recommended on RTX 5080) | text-to-video per scene | ~5 s 480p clips, first run downloads the model |
| Stable Video Diffusion | image-to-video (uploaded images / keyframes) | opt-in: set `stabilityai/stable-video-diffusion-img2vid-xt` and `huggingface-cli login` |

The **System readiness** panel on the dashboard shows which of these actually work on your machine.

### Existing features kept

* Original news pipeline (Quick mode on *News Feed Videos*), and the old UI at `/classic`.
* Original endpoints: `/news`, `/generate`, `/generate-content`, `/generate-story`, `/progress`, `/video`, `/upload_youtube`, `/open_folder` (now login-protected).
* YouTube publishing with your existing `youtube_token.json`.

**Security:** the Pexels and Pixabay keys that were hardcoded in the old `app.py` are still in git
history — rotate them and put the new ones in `.env`.

## Tests

```bash
python -m pytest -q
```
