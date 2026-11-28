# Installation & deployment

## A. Ubuntu development machine (RTX 5080)

```bash
sudo apt install -y python3.12 python3.12-venv ffmpeg espeak-ng tesseract-ocr \
     tesseract-ocr-mal tesseract-ocr-hin tesseract-ocr-tam tesseract-ocr-kan
cd ~/Projects/Videos/ai\ video          # your clone
python3.12 -m venv .venv && . .venv/bin/activate   # Python 3.12: Kokoro does not build on 3.13
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt -r requirements-mabcat.txt
pip install kokoro "piper-tts==1.2.0" silero-vad   # optional voice engines / VAD
cp -n .env.example .env                           # edit it
ollama pull llama3                                # teaching LLM (or set MABCAT_LLM_PROVIDER=openai|gemini)
uvicorn mabixai.backend.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/console/portal.html` (sign in `admin@mabix.local` / `admin` in development),
the teaching console at `/console/`, the speech console at `/console/stt.html`, API docs at `/api/docs`.
The existing video studio is still at `/dashboard` on the same port (`python app.py` still runs the
studio alone on :5000).

First-use model downloads (to `~/.cache/huggingface`): faster-whisper large-v3 ≈ 3 GB,
IndicConformer 600M ≈ 2.5 GB (may require accepting terms on its Hugging Face page and
`huggingface-cli login`), Kokoro ≈ 330 MB. Pre-download with `MABCAT_WARMUP=stt,tts`.

GPU check: `python -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.get_arch_list())"`
must list `sm_120`. faster-whisper uses CTranslate2; if it cannot use the GPU it falls back to CPU int8 and
says so (`device: cpu`) in every transcript.

## B. macOS development machine

```bash
brew install python@3.12 ffmpeg espeak-ng tesseract tesseract-lang
python3.12 -m venv .venv && . .venv/bin/activate
pip install torch torchvision torchaudio            # CPU/MPS build
pip install -r requirements.txt -r requirements-mabcat.txt
MABCAT_WHISPER_DEVICE=cpu MABCAT_WHISPER_COMPUTE=int8 MABCAT_WHISPER_MODEL=small \
  uvicorn mabixai.backend.main:app --port 8000
```
No CUDA on macOS: use a smaller Whisper model, or point `OLLAMA_HOST` / STT at the GPU PC.

## C. Docker (GPU)

Requires the NVIDIA Container Toolkit. `docker compose up -d --build` starts the app (port 8000) and
Ollama; `--profile postgres` adds PostgreSQL (set `DATABASE_URL` and `pip install psycopg[binary]`).
Models are cached in the `models` volume. **The Dockerfile has not been built yet** (no Docker daemon in
the build environment) — build and smoke-test it once before relying on it.

## D. Making the WebSocket reachable from the robot / outside

* Same Wi-Fi: run with `--host 0.0.0.0` and use `ws://<PC-LAN-IP>:8000/ws/mabcat/<device>`;
  allow the port in the firewall (`sudo ufw allow 8000/tcp`).
* Outside the LAN / production: put a TLS reverse proxy in front and use `wss://`. Caddy example:
  ```
  mabcat.example.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```
  Caddy obtains certificates automatically and proxies WebSockets without extra config.
  A quick test tunnel: `cloudflared tunnel --url http://localhost:8000` (gives a public https URL;
  use `wss://…/ws/mabcat/<device>`). Never expose the development defaults this way.

## E. Production checklist

* `MABIX_ENV=production`, strong `MABCAT_SECRET_KEY`, `MABIX_SECRET_KEY`, `MABCAT_ADMIN_PASSWORD`
  (the admin/admin default is refused), `MABIX_ADMIN_PASSWORD_HASH` for the studio.
* PostgreSQL via `DATABASE_URL` (SQLite is fine for a single robot / prototype) — **untested with
  PostgreSQL so far**; run the test suite against it first (`DATABASE_URL=… pytest mabixai/tests`).
* TLS everywhere (`wss://`), firewall the app port to the proxy only.
* Data retention: `MABCAT_STORE_AUDIO=0` (default), `MABCAT_AUDIO_RETENTION_HOURS`,
  `MABCAT_TRANSCRIPT_RETENTION_DAYS` (transcript purge job: not implemented yet — see TEST_REPORT).
* Backups of `storage/` (documents, media, SQLite) or PostgreSQL dumps.
* Monitoring: `GET /healthz` (liveness), `GET /api/v1/health` (LLM/STT/TTS availability, connected devices);
  logs go to stdout (`mabcat.*` loggers) — ship them with your log collector. Cloud STT spend:
  `GET /api/v1/analytics/overview` (`cloud_cost_usd`).
* Run with several uvicorn workers only after moving the device hub to Redis pub/sub — today live
  WebSocket state is per process, so use **one worker** (`--workers 1`).
