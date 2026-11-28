"""MabixAI RoboCat — FastAPI application.

Run:  uvicorn mabixai.backend.main:app --host 0.0.0.0 --port 8000
  /api/v1/*                 REST API (OpenAPI docs at /api/docs)
  /ws/mabcat/{device_id}    teaching WebSocket
  /ws/mabcat/audio/{id}     speech-to-text WebSocket
  /console/                 web testing consoles (teaching, speech, portal)
  /                         the existing Mabix AI video studio (Flask app, mounted unchanged)
"""
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import accounts, audio, curriculum, stt, teaching, websocket
from .config import settings
from .curriculum import materials
from .security import bootstrap_admin
from .speech.audio import recorder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mabcat")
CONSOLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web-console")


def create_app(mount_studio=True, database_url=None):
    app = FastAPI(title="MabixAI RoboCat API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  description="AI teacher platform: curriculum, teaching sessions, speech, devices. "
                              "WebSocket protocol: docs/mabcat/WEBSOCKET_PROTOCOL.md")
    db.configure(database_url)
    with db.session_scope() as s:
        bootstrap_admin(s)

    for r in (accounts.router, curriculum.router, teaching.router, stt.router, audio.router, websocket.router):
        app.include_router(r)

    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["health"])
    def health():
        from .speech.router import get_router
        from .speech.tts import engines as tts
        from .teaching import llm
        stt_s = get_router().status()["providers"]
        tts_s = tts.status()
        llm_s = llm.status()
        return {"status": "ok", "database": db.engine().url.get_backend_name(),
                "llm": {"provider": llm.provider(), "available": bool(llm_s["ok"]), "detail": llm_s["detail"]},
                "stt": {k: {"available": v["available"], "detail": v["detail"]} for k, v in stt_s.items()},
                "tts": {k: {"available": v["available"], "detail": v["detail"]} for k, v in tts_s.items() if v["enabled"]},
                "connected_devices": sorted(d for d, c in websocket.hub.conns.items() if c)}

    @app.on_event("startup")
    async def _startup():
        loop = asyncio.get_running_loop()

        def notify(device_id, msg):
            coro = websocket.hub.broadcast(device_id, msg, kind="teach") if device_id else \
                asyncio.gather(*[websocket.hub.broadcast(d, msg, kind="teach") for d in list(websocket.hub.conns)])
            asyncio.run_coroutine_threadsafe(coro, loop)
        materials.notify = notify

        async def housekeeping():
            while True:
                try:
                    n = recorder.purge()
                    if n:
                        log.info("purged %d temporary audio files", n)
                except Exception:
                    log.exception("audio purge failed")
                await asyncio.sleep(3600)
        asyncio.create_task(housekeeping())
        warm = os.environ.get("MABCAT_WARMUP", "")
        if warm:
            asyncio.create_task(asyncio.to_thread(_warmup, warm))

    if os.path.isdir(CONSOLE_DIR):
        app.mount("/console", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")

    @app.get("/mabcat", include_in_schema=False)
    def _mabcat():
        return RedirectResponse("/console/")

    if mount_studio:
        try:
            from a2wsgi import WSGIMiddleware
            from mabix import create_app as create_studio
            app.mount("/", WSGIMiddleware(create_studio()))
        except Exception as exc:  # the API still works without the studio
            log.warning("Video studio not mounted: %s", exc)
    return app


def _warmup(spec):
    """Load models before the first request (MABCAT_WARMUP=stt,tts)."""
    from .speech.router import get_router
    if "stt" in spec:
        for name, p in get_router().providers.items():
            ok, _ = p.status()
            if ok and p.local:
                try:
                    p.load()
                    log.info("warmed up STT %s", name)
                except Exception as exc:
                    log.warning("warm-up %s failed: %s", name, exc)
    if "tts" in spec:
        from .speech.tts import engines as tts
        try:
            list(tts.synthesize_stream("Hello!", "en"))
        except Exception as exc:
            log.warning("TTS warm-up failed: %s", exc)


app = create_app(mount_studio=os.environ.get("MABCAT_MOUNT_STUDIO", "1") != "0")
