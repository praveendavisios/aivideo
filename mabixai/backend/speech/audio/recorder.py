"""Temporary child-audio storage — off by default.

Raw audio is kept only when MABCAT_STORE_AUDIO=1 AND the parent gave
consent_audio, and is deleted after MABCAT_AUDIO_RETENTION_HOURS.
Transcripts (text) are what the platform keeps.
"""
import os
import time

from ...config import settings
from .preprocessing import to_wav_bytes

DIR = os.path.join(settings.DATA_DIR, "audio_tmp")


def save(audio, session_id, utterance_id, consent):
    if not (settings.STORE_AUDIO and consent):
        return None
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, f"{session_id}_{utterance_id}.wav")
    with open(path, "wb") as fh:
        fh.write(to_wav_bytes(audio))
    return path


def purge(max_age_hours=None):
    max_age = (max_age_hours if max_age_hours is not None else settings.AUDIO_RETENTION_HOURS) * 3600
    if not os.path.isdir(DIR):
        return 0
    now, n = time.time(), 0
    for f in os.listdir(DIR):
        p = os.path.join(DIR, f)
        if os.path.isfile(p) and now - os.path.getmtime(p) > max_age:
            os.remove(p)
            n += 1
    return n
