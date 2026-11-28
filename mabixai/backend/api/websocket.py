"""WebSocket endpoints.

/ws/mabcat/{device_id}        — teaching session: text/voice chat, lessons, quizzes, TTS audio, robot commands
/ws/mabcat/audio/{device_id}  — dedicated real-time speech-to-text stream

Both authenticate with the FIRST message (tokens never appear in URLs / access logs):
  {"type": "auth", "token": "<device token>"}            robot firmware
  {"type": "auth", "user_token": "<login token>"}        web console (user must own the device)
See docs/mabcat/WEBSOCKET_PROTOCOL.md for every message.
"""
import asyncio
import json
import logging
import threading
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..db import session_scope, utcnow
from ..models import Device, Student, TeachingSession
from ..security import audit, authenticate_device, user_from_token
from ..speech.audio.preprocessing import AudioError
from ..speech.session_manager import AudioSession
from ..speech.tts import engines as tts
from ..teaching import student_engine
from ..teaching.teacher import TeacherEngine, expression
from . import protocol

log = logging.getLogger("mabcat.ws")
router = APIRouter()
AUTH_TIMEOUT = 10


class Hub:
    """Live connections per device (robot, consoles, audio sockets)."""

    def __init__(self):
        self.conns = {}

    def add(self, device_id, conn):
        self.conns.setdefault(device_id, set()).add(conn)

    def remove(self, device_id, conn):
        self.conns.get(device_id, set()).discard(conn)

    def peers(self, device_id, kind=None):
        return [c for c in self.conns.get(device_id, set()) if kind is None or c.kind == kind]

    async def broadcast(self, device_id, msg, kind=None, exclude=None):
        for c in self.peers(device_id, kind):
            if c is not exclude:
                await c.send(msg)

    def teaching_session(self, device_id):
        for c in self.peers(device_id, "teach"):
            if c.session_id:
                return c
        return None


hub = Hub()


class Conn:
    def __init__(self, ws: WebSocket, device_id, kind):
        self.ws, self.device_id, self.kind = ws, device_id, kind
        self.lock = asyncio.Lock()
        self.student_id = None
        self.session_id = None
        self.tts_task = None
        self.tts_enabled = True
        self.tts_rate = 16000
        self.tts_stream = 0
        self.audio = None
        self.last_activity = time.time()
        self.closed = False

    async def send(self, msg):
        if self.closed:
            return
        async with self.lock:
            try:
                if isinstance(msg, (bytes, bytearray)):
                    await self.ws.send_bytes(bytes(msg))
                else:
                    await self.ws.send_text(json.dumps(msg, default=str, ensure_ascii=False))
            except Exception:
                self.closed = True

    async def error(self, code, message, **extra):
        await self.send({"type": "error", "code": code, "message": message, **extra})


async def _authenticate(ws, device_id):
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, ValueError):
        return None, "auth_timeout"
    if msg.get("type") != "auth":
        return None, "auth_required"
    with session_scope() as db:
        if msg.get("token"):
            d = authenticate_device(db, device_id, msg["token"])
            if d:
                d.last_seen = utcnow()
                d.firmware = str(msg.get("firmware", d.firmware))[:40]
                audit(db, device_id, "device.connect")
                return {"device": device_id, "student_id": d.student_id, "actor": device_id}, None
        if msg.get("user_token"):
            u = user_from_token(db, msg["user_token"])
            d = db.get(Device, device_id)
            if u and d and d.enabled and (d.owner_id == u.id or u.role == "admin"):
                audit(db, u.id, "console.connect", device_id)
                return {"device": device_id, "student_id": d.student_id, "actor": u.id}, None
    return None, "auth_failed"


def _student_ok(db, device_id, student_id, need_audio=False):
    d = db.get(Device, device_id)
    st = db.get(Student, student_id) if student_id else None
    if not st or not d or st.parent_id != d.owner_id:
        return None, ("student_not_found", "Student not linked to this device's account")
    if not st.consent_learning:
        return None, ("consent_required", "A parent must give consent before Mab Cat can teach this student")
    if need_audio and not st.consent_audio:
        return None, ("audio_consent_required", "A parent must allow voice processing for this student")
    if student_engine.over_limit(db, st):
        return None, ("usage_limit_reached", f"Daily learning time of {st.daily_limit_minutes} minutes reached")
    return st, None


def _run_teacher(session_id, method, *args):
    """Run a TeacherEngine call in its own DB transaction (called in a worker thread)."""
    with session_scope() as db:
        s = db.get(TeachingSession, session_id)
        if not s or s.status != "active":
            return [{"type": "error", "code": "session_not_active", "message": "Start a session first"}]
        st = db.get(Student, s.student_id)
        engine = TeacherEngine(db, s, st)
        out = getattr(engine, method)(*args)
        now = utcnow()
        last = s.state.get("_last") if isinstance(s.state, dict) else None
        s.active_seconds += 30 if not last else min(120, max(0, int(now.timestamp() - last)))
        s.state = {**engine.state, "_last": now.timestamp()}
        if student_engine.over_limit(db, st):
            out.append({"type": "usage_limit_reached", "session_id": s.id, "minutes": st.daily_limit_minutes})
        return out


SPOKEN = ("teacher_response", "quiz_question")


async def deliver(conn, messages, language="en"):
    """Send teacher messages, then stream TTS for the spoken ones (cancellable)."""
    spoken = []
    for m in messages:
        if m.get("type") in SPOKEN and m.get("text"):
            m["audio_status"] = "generating" if conn.tts_enabled else "disabled"
            spoken.append(m)
        await conn.send(m)
        if m.get("type") == "robot_expression":
            await hub.broadcast(conn.device_id, m, kind="teach", exclude=conn)
    if spoken and conn.tts_enabled:
        if conn.tts_task and not conn.tts_task.done():
            conn.tts_task.cancel()
        conn.tts_task = asyncio.create_task(_speak(conn, spoken, language))


async def _speak(conn, messages, language):
    conn.tts_stream += 1
    sid = conn.tts_stream
    seq = 0
    text = " ".join(m["text"] for m in messages)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stop = threading.Event()

    def produce():
        try:
            for part in tts.synthesize_stream(text, language, out_rate=conn.tts_rate):
                if stop.is_set():
                    return
                loop.call_soon_threadsafe(queue.put_nowait, part)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, produce)
    started = False
    try:
        while True:
            part = await queue.get()
            if part is None:
                break
            if isinstance(part, Exception):
                await conn.send({"type": "tts_error", "stream_id": sid, "message": str(part)})
                break
            if not started:
                started = True
                await conn.send({"type": "tts_audio_start", "stream_id": sid, "format": "pcm_s16le",
                                 "sample_rate": part["sample_rate"], "channels": 1, "engine": part["engine"],
                                 "session_id": conn.session_id})
                await conn.send(expression("speaking"))
                if conn.audio:
                    conn.audio.robot_speaking = True
            await conn.send({"type": "tts_sentence", "stream_id": sid, "text": part["text"], "language": part["language"]})
            pcm = part["pcm"]
            for i in range(0, len(pcm), 4096):  # 128 ms per frame at 16 kHz
                await conn.send(protocol.pack(protocol.KIND_TTS, sid, seq, pcm[i:i + 4096]))
                seq += 1
        if started:
            await conn.send({"type": "tts_audio_end", "stream_id": sid, "frames": seq})
    except asyncio.CancelledError:
        stop.set()  # stop synthesising the remaining sentences
        await conn.send({"type": "tts_audio_end", "stream_id": sid, "frames": seq, "interrupted": True})
        raise


# ---------------------------------------------------------- teaching socket
@router.websocket("/ws/mabcat/{device_id}")
async def teaching_ws(ws: WebSocket, device_id: str):
    await ws.accept()
    ident, err = await _authenticate(ws, device_id)
    if not ident:
        await ws.send_text(json.dumps({"type": "error", "code": err, "message": "Authentication failed"}))
        await ws.close(code=4401)
        return
    conn = Conn(ws, device_id, "teach")
    conn.student_id = ident["student_id"]
    hub.add(device_id, conn)
    await conn.send({"type": "connected", "device_id": device_id, "protocol": protocol.PROTOCOL_VERSION,
                     "heartbeat_s": settings.HEARTBEAT_S, "student_id": conn.student_id,
                     "audio_frame": "MC header (12 bytes) + PCM; see protocol docs"})
    await conn.send(expression("neutral"))
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive(), timeout=settings.HEARTBEAT_S * 3)
            except asyncio.TimeoutError:
                await conn.error("heartbeat_timeout", "No heartbeat received")
                break
            if data.get("type") == "websocket.disconnect":
                break
            conn.last_activity = time.time()
            if data.get("bytes") is not None:
                await _teach_audio_bytes(conn, data["bytes"])
                continue
            try:
                msg = json.loads(data.get("text") or "{}")
            except ValueError:
                await conn.error("bad_json", "Messages must be JSON")
                continue
            try:
                await _teach_message(conn, msg)
            except protocol.FrameError as exc:
                await conn.error("bad_message", str(exc))
            except AudioError as exc:
                await conn.error("bad_audio", str(exc))
            except Exception as exc:
                log.exception("ws handler error")
                await conn.error("server_error", f"{exc.__class__.__name__}: {exc}")
    except WebSocketDisconnect:
        pass
    finally:
        conn.closed = True
        hub.remove(device_id, conn)
        if conn.tts_task:
            conn.tts_task.cancel()


async def _teach_audio_bytes(conn, frame):
    try:
        kind, _, seq, payload = protocol.unpack(frame)
    except protocol.FrameError as exc:
        await conn.error("bad_frame", str(exc))
        return
    if kind != protocol.KIND_MIC or not conn.audio:
        await conn.error("audio_not_started", "Send audio_start before microphone frames")
        return
    await conn.audio.feed(payload, seq)


async def _start_session(conn, msg):
    student_id = msg.get("student_id") or conn.student_id
    with session_scope() as db:
        st, err = _student_ok(db, conn.device_id, student_id)
        if err:
            return None, err
        s = TeachingSession(id=msg.get("session_id") or f"ses_{uuid.uuid4().hex[:10]}", student_id=st.id,
                            device_id=conn.device_id, curriculum_id=msg.get("curriculum_id"),
                            state={"mode": "chat", "persona": msg.get("persona")} if msg.get("persona") else {"mode": "chat"})
        db.add(s)
        audit(db, conn.device_id, "session.start", s.id, student=st.id)
        return {"id": s.id, "student": st.display_name, "language": st.preferred_language,
                "next": student_engine.next_node(db, st)}, None


async def _teach_message(conn, msg):
    t = msg.get("type")
    if t == "heartbeat":
        await conn.send({"type": "heartbeat_ack", "server_time": time.time(), "session_id": conn.session_id})
        return
    if t == "session_start":
        info, err = await _start_session(conn, msg)
        if err:
            await conn.error(*err)
            if err[0] == "usage_limit_reached":
                await conn.send({"type": "usage_limit_reached", "message": err[1]})
            return
        conn.session_id, conn.student_id = info["id"], msg.get("student_id") or conn.student_id
        conn.tts_enabled = msg.get("tts", True) is not False
        conn.tts_rate = int(msg.get("tts_sample_rate", 16000))
        conn.language = msg.get("language") or info["language"]
        await conn.send({"type": "session_started", "session_id": conn.session_id, "student": info["student"],
                         "next_lesson": info["next"]})
        if msg.get("node_id"):
            await deliver(conn, await asyncio.to_thread(_run_teacher, conn.session_id, "start_lesson", msg["node_id"]),
                          conn.language)
        else:
            await deliver(conn, [expression("happy"), {"type": "teacher_response", "session_id": conn.session_id,
                                                       "text": f"Hi {info['student']}! I'm Mab Cat. What would you like to learn today?",
                                                       "emotion": "happy", "engine": "system", "sources": []}],
                          conn.language)
        return
    if t in ("robot_expression", "ear_move"):
        cmd = protocol.robot_command(msg)
        await conn.send(cmd | {"echo": True})
        await hub.broadcast(conn.device_id, cmd, kind="teach", exclude=conn)
        return
    if t in ("playback_started", "playback_finished"):
        if conn.audio:
            conn.audio.robot_speaking = t == "playback_started"
        return
    if t == "interrupt":
        if conn.tts_task and not conn.tts_task.done():
            conn.tts_task.cancel()
        await conn.send(expression("listening"))
        return
    if not conn.session_id:
        await conn.error("no_session", "Send session_start first")
        return
    if t == "start_lesson":
        out = await asyncio.to_thread(_run_teacher, conn.session_id, "start_lesson", msg.get("node_id"))
        await deliver(conn, out, getattr(conn, "language", "en"))
    elif t in ("student_message", "student_answer"):
        text = msg.get("text") or ""
        if t == "student_answer" and "option_index" in msg and msg.get("options"):
            text = str(msg["options"][int(msg["option_index"])])
        if not text.strip():
            await conn.error("empty_message", "text is required")
            return
        if conn.tts_task and not conn.tts_task.done():  # the student spoke → stop talking
            conn.tts_task.cancel()
        out = await asyncio.to_thread(_run_teacher, conn.session_id, "student_message", text[:2000], msg.get("language"),
                                      {"input": msg.get("input", "text")})
        await deliver(conn, out, msg.get("language") or getattr(conn, "language", "en"))
    elif t == "tts_request":
        await deliver(conn, [{"type": "teacher_response", "session_id": conn.session_id, "text": str(msg.get("text", ""))[:1000],
                              "emotion": "speaking", "engine": "tts-test", "sources": []}], msg.get("language", "en"))
    elif t == "audio_start":
        await _start_audio(conn, msg, forward=True)
    elif t == "audio_chunk":
        if not conn.audio:
            await conn.error("audio_not_started", "Send audio_start first")
            return
        await conn.audio.feed(protocol.b64_audio(msg), msg.get("sequence"))
    elif t in ("speech_ended", "audio_stop"):
        if conn.audio:
            await conn.audio.speech_ended()
            if t == "audio_stop":
                await conn.audio.stop()
                await conn.send({"type": "audio_stopped", **conn.audio.stats()})
                conn.audio = None
    elif t == "session_end":
        await _end_session(conn)
    else:
        await conn.error("unknown_type", f"Unknown message type {t!r}")


async def _end_session(conn):
    with session_scope() as db:
        s = db.get(TeachingSession, conn.session_id)
        st = db.get(Student, s.student_id)
        s.status, s.ended_at = "ended", utcnow()
        summary = student_engine.summary(db, st)
    await conn.send({"type": "session_ended", "session_id": conn.session_id, "summary": summary})
    await conn.send(expression("sleeping"))
    conn.session_id = None


async def _start_audio(conn, msg, forward):
    with session_scope() as db:
        st, err = _student_ok(db, conn.device_id, msg.get("student_id") or conn.student_id, need_audio=True)
        if err:
            await conn.error(*err)
            return None
        db.expunge(st)

    async def on_final(ev):
        if not forward:
            return
        target = conn if conn.kind == "teach" else hub.teaching_session(conn.device_id)
        if not target or not target.session_id:
            await conn.send({"type": "processing_status", "status": "no_teaching_session",
                             "message": "Transcript not sent to the AI teacher: no active teaching session"})
            return
        await target.send({"type": "student_transcript", "session_id": target.session_id, "text": ev["text"],
                           "language": ev["language"], "provider": ev["provider"]})
        out = await asyncio.to_thread(_run_teacher, target.session_id, "student_message", ev["text"], ev["language"],
                                      {"input": "voice", "stt_provider": ev["provider"], "stt_model": ev.get("model"),
                                       "utterance_id": ev["utterance_id"]})
        await deliver(target, out, (ev["language"] or "en").split("-")[0])

    conn.audio = AudioSession(conn.send, conn.device_id, st, sample_rate=msg.get("sample_rate", 16000),
                              channels=msg.get("channels", 1), audio_format=msg.get("audio_format", "pcm_s16le"),
                              language=msg.get("language", "auto"), provider=msg.get("provider"), on_final=on_final,
                              session_id=msg.get("session_id"))
    await conn.send({"type": "session_ready" if conn.kind == "audio" else "audio_ready", "session_id": conn.audio.id,
                     "stream_id": int(msg.get("stream_id", 0)), "sample_rate_in": conn.audio.sr,
                     "internal_format": "pcm_s16le 16 kHz mono", "vad": conn.audio.vad.name,
                     "end_of_utterance_ms": settings.END_OF_UTTERANCE_MS})
    return conn.audio


# ------------------------------------------------------------- audio socket
@router.websocket("/ws/mabcat/audio/{device_id}")
async def audio_ws(ws: WebSocket, device_id: str):
    await ws.accept()
    ident, err = await _authenticate(ws, device_id)
    if not ident:
        await ws.send_text(json.dumps({"type": "transcription_error", "code": err, "message": "Authentication failed"}))
        await ws.close(code=4401)
        return
    conn = Conn(ws, device_id, "audio")
    conn.student_id = ident["student_id"]
    hub.add(device_id, conn)
    await conn.send({"type": "connected", "device_id": device_id, "protocol": protocol.PROTOCOL_VERSION})
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive(), timeout=settings.HEARTBEAT_S * 3)
            except asyncio.TimeoutError:
                break
            if data.get("type") == "websocket.disconnect":
                break
            try:
                if data.get("bytes") is not None:
                    if not conn.audio:
                        await conn.send({"type": "transcription_error", "code": "no_session", "message": "Send session_start first"})
                        continue
                    kind, _, seq, payload = protocol.unpack(data["bytes"])
                    if kind == protocol.KIND_MIC:
                        await conn.audio.feed(payload, seq)
                    continue
                msg = json.loads(data.get("text") or "{}")
                t = msg.get("type")
                if t == "heartbeat":
                    await conn.send({"type": "heartbeat_ack", "server_time": time.time()})
                elif t == "session_start":
                    if conn.audio:
                        await conn.audio.stop()
                    await _start_audio(conn, msg, forward=msg.get("forward_to_teacher", True))
                elif t == "audio_chunk":
                    if conn.audio:
                        await conn.audio.feed(protocol.b64_audio(msg), msg.get("sequence"))
                elif t == "speech_detected":
                    pass  # client-side VAD hint; the server runs its own VAD
                elif t == "speech_ended":
                    if conn.audio:
                        await conn.audio.speech_ended()
                elif t == "playback_started" and conn.audio:
                    conn.audio.robot_speaking = True
                elif t == "playback_finished" and conn.audio:
                    conn.audio.robot_speaking = False
                elif t == "session_stop":
                    if conn.audio:
                        await conn.audio.stop()
                        await conn.send({"type": "session_stopped", **conn.audio.stats()})
                        conn.audio = None
                else:
                    await conn.send({"type": "transcription_error", "code": "unknown_type", "message": f"Unknown type {t!r}"})
            except (protocol.FrameError, AudioError, ValueError) as exc:
                await conn.send({"type": "transcription_error", "code": "bad_input", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        conn.closed = True
        hub.remove(device_id, conn)
        if conn.audio:
            await conn.audio.stop()
