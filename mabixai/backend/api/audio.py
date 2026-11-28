"""Text-to-speech REST API and health."""
import asyncio

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response

from ..speech.audio.preprocessing import pcm_to_float, to_wav_bytes
from ..speech.tts import engines as tts
from ..security import current_user

router = APIRouter(prefix="/api/v1/tts", tags=["text-to-speech"])


@router.get("/status")
def status(user=Depends(current_user)):
    return tts.status()


@router.post("/synthesize")
async def synthesize(text: str = Form(..., max_length=2000), language: str = Form("en"), engine: str | None = Form(None),
                     user=Depends(current_user)):
    """Returns a 16 kHz WAV of the whole text (the WebSocket streams it sentence by sentence instead)."""
    def run():
        parts = list(tts.synthesize_stream(text, language, prefer=engine, out_rate=16000))
        pcm = b"".join(p["pcm"] for p in parts)
        return to_wav_bytes(pcm_to_float(pcm), 16000), ",".join(sorted({p["engine"] for p in parts}))
    try:
        wav, used = await asyncio.to_thread(run)
    except tts.TTSUnavailable as exc:
        raise HTTPException(503, str(exc))
    return Response(wav, media_type="audio/wav", headers={"X-TTS-Engine": used})
