"""Speech-to-text REST API (status, one-shot transcription, benchmark results)."""
import asyncio
import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..security import current_user, get_student_for, require
from ..speech import session_manager
from ..speech.audio import vad as vadmod
from ..speech.audio.preprocessing import AudioError
from ..speech.language import code_switching
from ..speech.providers.base import ProviderUnavailable
from ..speech.router import get_router

router = APIRouter(prefix="/api/v1/stt", tags=["speech-to-text"])


@router.get("/status")
def status(user=Depends(current_user)):
    s = get_router().status()
    s["vad"] = vadmod.create(settings.VAD_ENGINE).name
    s["noise_reduction"] = settings.NOISE_REDUCTION
    s["end_of_utterance_ms"] = settings.END_OF_UTTERANCE_MS
    s["store_audio"] = settings.STORE_AUDIO
    return s


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("auto"), provider: str | None = Form(None),
                     student_id: str | None = Form(None), db: Session = Depends(get_db), user=Depends(current_user)):
    """Transcribe an uploaded recording (wav/webm/ogg/mp3/m4a). Original language is preserved — no translation."""
    student = None
    if student_id:
        student = get_student_for(db, user, student_id)
        if not student.consent_audio:
            raise HTTPException(403, "Parent has not consented to voice processing for this student")
    data = await file.read(settings.MAX_AUDIO_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        return await asyncio.wait_for(asyncio.to_thread(session_manager.transcribe_file, data, language, provider, student),
                                      timeout=settings.STT_TIMEOUT_S * 4)
    except AudioError as exc:
        raise HTTPException(422, str(exc))
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(504, "Transcription timed out")


@router.post("/analyse-text")
def analyse_text(text: str = Form(...), user=Depends(current_user)):
    """Code-switching analysis of a transcript (per-word language)."""
    return code_switching.analyse(text) | {"label": code_switching.label(text)}


@router.get("/benchmark")
def benchmark_report(user=Depends(require("admin", "teacher"))):
    path = os.path.join(settings.DATA_DIR, "benchmarks", "latest.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No benchmark run yet — run: python -m mabixai.backend.speech.benchmark <manifest>")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
