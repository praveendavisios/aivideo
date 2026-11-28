"""Integration tests for the RoboCat backend.

Real: FastAPI routes, SQLite, WebSockets (ASGI), PDF/DOCX/PPTX parsing, Tesseract OCR,
espeak-ng speech audio, VAD/segmentation/resampling, TTS streaming, binary frames.
Faked (clearly): the STT *model* (a "test-stt" provider registered under its own name)
and, in one test, the LLM — neither model can be downloaded in CI.
"""
import base64
import json
import os
import struct
import subprocess
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mabixai.backend.api import protocol
from mabixai.backend.speech.audio import preprocessing as pp
from mabixai.backend.speech.providers.base import ProviderUnavailable, STTProvider, Transcript
from mabixai.tests import fixtures

DOCS = {}


class FakeSTT(STTProvider):
    """Test-only provider: returns the scripted text for each utterance, in order."""
    name = "test-stt"
    languages = None

    def __init__(self, texts=None):
        self.texts = list(texts or [])
        self.calls = []

    def status(self):
        return True, "test provider"

    def transcribe(self, audio, language=None, sample_rate=16000):
        self.calls.append({"duration": len(audio) / sample_rate, "language": language})
        text = self.texts.pop(0) if self.texts else "hello"
        return Transcript(text=text, language=language or "en", provider=self.name, model="scripted",
                          duration=round(len(audio) / sample_rate, 2), latency=0.01)


class FailingSTT(FakeSTT):
    name = "broken-stt"

    def transcribe(self, *a, **k):
        raise ProviderUnavailable("GPU out of memory")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    d = tmp_path_factory.mktemp("docs")
    DOCS["pdf"] = fixtures.make_pdf(str(d / "Grade5_Science_Chapter3.pdf"))
    DOCS["docx"] = fixtures.make_docx(str(d / "chapter3.docx"))
    DOCS["pptx"] = fixtures.make_pptx(str(d / "magnets.pptx"))
    DOCS["scan"] = fixtures.make_scanned_pdf(str(d / "scanned_water.pdf"), str(d))
    from mabixai.backend.main import create_app
    return create_app(mount_studio=True, database_url=f"sqlite:///{d}/mabcat.db")


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


def login(client, email, pw):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def world(client):
    """admin, teacher, parent, consenting student, device, uploaded+processed curriculum, class, assignment."""
    admin = login(client, "admin@mabix.local", "admin")
    for email, role in (("teacher@school.test", "teacher"), ("parent@home.test", "parent"), ("other@home.test", "parent")):
        assert client.post("/api/v1/users", headers=admin, json={"email": email, "role": role, "password": "secret-pass"}).status_code == 200
    teacher, parent = login(client, "teacher@school.test", "secret-pass"), login(client, "parent@home.test", "secret-pass")
    other = login(client, "other@home.test", "secret-pass")
    st = client.post("/api/v1/students", headers=parent, json={"display_name": "Anu", "grade": "5", "age": 10,
                                                                "preferred_language": "en"}).json()
    dev = client.post("/api/v1/devices", headers=parent, json={"device_id": "mabcat-001", "student_id": st["id"]}).json()
    files = [("files", (os.path.basename(p), open(p, "rb"))) for p in (DOCS["pdf"], DOCS["docx"], DOCS["pptx"], DOCS["scan"])]
    cur = client.post("/api/v1/curricula", headers=teacher, data={"title": "Grade 5 Science", "language": "en"}, files=files).json()
    for _ in range(100):
        detail = client.get(f"/api/v1/curricula/{cur['id']}", headers=teacher).json()
        if detail["status"] != "processing" and all(d["status"] in ("ready", "failed") for d in detail["documents"]):
            break
        time.sleep(0.2)
    cls = client.post("/api/v1/classes", headers=teacher, params={"name": "5A"}).json()
    assert client.post("/api/v1/classes/join", headers=parent, params={"join_code": cls["join_code"], "student_id": st["id"]}).status_code == 200
    assert client.post("/api/v1/assignments", headers=teacher, params={"student_id": st["id"], "curriculum_id": cur["id"]}).status_code == 200
    return {"admin": admin, "teacher": teacher, "parent": parent, "other": other, "student": st, "device": dev,
            "curriculum": detail, "class": cls}


def find(tree, title_part):
    for n in tree:
        if title_part.lower() in n["title"].lower():
            return n
        hit = find(n["children"], title_part)
        if hit:
            return hit
    return None


def ws_auth(ws, token):
    ws.send_text(json.dumps({"type": "auth", "token": token}))
    return json.loads(ws.receive_text())


def recv_spoken(ws):
    """Wait for the end of the *current* TTS stream (interrupted older streams also send tts_audio_end)."""
    while True:
        got, frames = recv_until(ws, {"tts_audio_end"})
        if not got[-1].get("interrupted"):
            return got, frames


def recv_until(ws, types, limit=400):
    """Collect messages (JSON and binary) until one of `types` arrives."""
    got, frames = [], []
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes") is not None:
            frames.append(m["bytes"])
            continue
        if m.get("text") is None:
            continue
        msg = json.loads(m["text"])
        got.append(msg)
        if msg["type"] in types:
            return got, frames
        if msg["type"] == "error" and "error" not in types:
            raise AssertionError(f"server error while waiting for {types}: {msg}")
    raise AssertionError(f"never received {types}; got {[g['type'] for g in got]}")


# ---------------------------------------------------------------- platform
def test_auth_and_roles(client, world):
    assert client.get("/api/v1/students").status_code == 401
    assert client.post("/api/v1/users", headers=world["parent"], json={"email": "x@y.z", "role": "admin", "password": "123456"}).status_code == 403
    assert client.post("/api/v1/auth/login", json={"email": "parent@home.test", "password": "wrong"}).status_code == 401
    me = client.get("/api/v1/auth/me", headers=world["teacher"]).json()
    assert me["role"] == "teacher"


def test_student_isolation(client, world):
    sid = world["student"]["id"]
    assert client.get(f"/api/v1/students/{sid}/progress", headers=world["other"]).status_code == 404
    assert client.get(f"/api/v1/students/{sid}/progress", headers=world["teacher"]).status_code == 200  # via class
    assert client.put(f"/api/v1/students/{sid}/consent", headers=world["teacher"], json={"learning": True, "audio": True}).status_code == 404
    assert world["student"]["id"] not in [s["id"] for s in client.get("/api/v1/students", headers=world["other"]).json()]


def test_device_token_shown_once_and_hashed(client, world):
    devs = client.get("/api/v1/devices", headers=world["parent"]).json()
    assert world["device"]["device_token"].startswith("mcd_")
    assert all("device_token" not in d and "token_hash" not in d for d in devs)


# -------------------------------------------------------------- curriculum
def test_curriculum_extraction_all_formats(world):
    cur = world["curriculum"]
    assert cur["status"] == "ready", cur
    docs = {d["filename"]: d for d in cur["documents"]}
    assert all(d["status"] == "ready" for d in docs.values()), docs
    assert docs["scanned_water.pdf"]["method"] == "ocr"
    assert cur["subject"] == "Science" and cur["grade"] == "5"
    tree = cur["tree"]
    assert tree[0]["level"] == "subject" and tree[0]["children"][0]["level"] == "grade"
    photo = find(tree, "Photosynthesis")
    leaf = find(tree, "Parts of a Leaf")
    assert photo["counts"]["definitions"] >= 2 and leaf["counts"]["questions"] == 2 and leaf["counts"]["activities"] == 1
    assert find(tree, "Magnetic Poles") and find(tree, "Chapter 4: Water")


def test_rag_search_has_sources(client, world):
    cid = world["curriculum"]["id"]
    r = client.get(f"/api/v1/curricula/{cid}/search", headers=world["teacher"], params={"q": "what is chlorophyll"}).json()
    top = r["results"][0]
    assert "chlorophyll" in top["text"].lower() and top["source"]["page"] >= 1 and top["source"]["filename"]
    ocr = client.get(f"/api/v1/curricula/{cid}/search", headers=world["teacher"], params={"q": "evaporation vapour"}).json()
    assert ocr["results"][0]["source"]["filename"] == "scanned_water.pdf"
    none = client.get(f"/api/v1/curricula/{cid}/search", headers=world["teacher"], params={"q": "volcano lava"}).json()
    assert none["results"] == []  # nothing invented


def test_upload_validation(client, world):
    bad = client.post("/api/v1/curricula", headers=world["teacher"], data={"title": "x"},
                      files=[("files", ("virus.exe", b"MZ...", "application/octet-stream"))])
    assert bad.status_code == 415
    fake = client.post("/api/v1/curricula", headers=world["teacher"], data={"title": "x"},
                       files=[("files", ("notreally.pdf", b"hello world", "application/pdf"))])
    assert fake.status_code == 422


def test_lesson_plan_offline_grounded_edit_approve(client, world):
    node = find(world["curriculum"]["tree"], "Parts of a Leaf")
    p = client.post(f"/api/v1/nodes/{node['id']}/plans", headers=world["teacher"]).json()
    assert p["generator"] == "offline" and p["status"] == "draft"
    qs = p["content"]["questions"]
    assert qs[0]["question"] == "What do plants need to make food?"
    assert qs[0]["expected_answer"].startswith("Sunlight")
    assert p["content"]["sources"] and p["content"]["activities"]
    content = p["content"] | {"introduction": "Let's explore leaves!"}
    r = client.patch(f"/api/v1/plans/{p['id']}", headers=world["teacher"], json={"content": content, "status": "approved"}).json()
    assert r["status"] == "approved" and r["content"]["introduction"] == "Let's explore leaves!"
    assert client.patch(f"/api/v1/plans/{p['id']}", headers=world["other"], json={"status": "draft"}).status_code == 404


def test_materials_text(client, world):
    node = find(world["curriculum"]["tree"], "Photosynthesis")
    fc = client.post(f"/api/v1/nodes/{node['id']}/materials", headers=world["teacher"], params={"kind": "flashcards"}).json()
    assert fc["status"] == "ready" and any(c["front"] == "Photosynthesis" for c in fc["content"]["cards"])
    leaf = find(world["curriculum"]["tree"], "Parts of a Leaf")
    qz = client.post(f"/api/v1/nodes/{leaf['id']}/materials", headers=world["teacher"], params={"kind": "quiz"}).json()
    assert qz["content"]["items"] and qz["content"]["items"][0]["answer"]


def test_video_material_uses_existing_studio(client, world):
    from mabix import repo
    node = find(world["curriculum"]["tree"], "Photosynthesis")
    m = client.post(f"/api/v1/nodes/{node['id']}/materials", headers=world["teacher"], params={"kind": "video"}).json()
    v = repo.get_video(m["studio_video_id"])
    assert v["video_type"] == "answer" and "Photosynthesis" in v["prompt"]
    jobs = repo.list_jobs(video_id=v["id"])["items"]
    assert {j["kind"] for j in jobs} == {"script", "render"}
    assert client.get("/login").status_code == 200  # studio UI still served


# -------------------------------------------------------- teaching websocket
def test_ws_requires_auth(client):
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "mcd_wrong"}))
        assert json.loads(ws.receive_text())["code"] == "auth_failed"


def test_ws_consent_required_then_full_lesson_loop(client, world):
    sid, token = world["student"]["id"], world["device"]["device_token"]
    leaf = find(world["curriculum"]["tree"], "Parts of a Leaf")
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        assert ws_auth(ws, token)["type"] == "connected"
        ws.send_text(json.dumps({"type": "session_start", "student_id": sid}))
        got, _ = recv_until(ws, {"error"})
        assert got[-1]["code"] == "consent_required"
    client.put(f"/api/v1/students/{sid}/consent", headers=world["parent"], json={"learning": True, "audio": True})

    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws_auth(ws, token)
        ws.send_text(json.dumps({"type": "session_start", "student_id": sid, "session_id": "session-001",
                                 "tts_sample_rate": 16000}))
        got, _ = recv_until(ws, {"session_started"})
        assert got[-1]["session_id"] == "session-001"
        got, frames = recv_spoken(ws)  # greeting is spoken
        assert any(g["type"] == "teacher_response" and "Anu" in g["text"] for g in got)
        start = next(g for g in got if g["type"] == "tts_audio_start")
        kind, stream, seq, payload = protocol.unpack(frames[0])
        assert kind == protocol.KIND_TTS and seq == 0 and start["sample_rate"] == 16000 and len(payload) > 0

        ws.send_text(json.dumps({"type": "start_lesson", "node_id": leaf["id"]}))
        got, _ = recv_spoken(ws)
        types = [g["type"] for g in got]
        assert "lesson_update" in types and "robot_expression" in types
        lesson = next(g for g in got if g["type"] == "lesson_update")
        assert lesson["questions"] == 3  # 2 from the exercises + 1 built from the extracted definition
        q1 = next(g for g in got if g["type"] == "quiz_question")
        assert q1["text"] == "What do plants need to make food?"
        intro = next(g for g in got if g["type"] == "teacher_response")
        assert "Let's explore leaves!" in intro["text"]  # the approved, teacher-edited plan is used

        # wrong answer → hint, same question again (answer not revealed)
        ws.send_text(json.dumps({"type": "student_answer", "text": "chocolate"}))
        got, _ = recv_spoken(ws)
        ev = next(g for g in got if g["type"] == "answer_evaluation")
        assert ev["correct"] is False and ev["attempt"] == 1
        assert "carbon dioxide" not in " ".join(g["text"] for g in got if g["type"] == "teacher_response").lower()
        assert not any(g["type"] == "quiz_question" for g in got)

        # a question instead of an answer → helped, then asked again
        ws.send_text(json.dumps({"type": "student_message", "text": "What is stomata?"}))
        got, _ = recv_spoken(ws)
        say = " ".join(g["text"] for g in got if g["type"] == "teacher_response")
        assert "openings" in say.lower() and "back to our question" in say.lower()

        # correct answer → next question
        ws.send_text(json.dumps({"type": "student_answer", "text": "They need sunlight, water and carbon dioxide"}))
        got, _ = recv_spoken(ws)
        ev = next(g for g in got if g["type"] == "answer_evaluation")
        assert ev["correct"] is True and ev["evaluator"] == "keyword-overlap"
        assert next(g for g in got if g["type"] == "quiz_question")["question_index"] == 2

        # "I don't know" twice → hint, then a gentle reveal and the next question
        ws.send_text(json.dumps({"type": "student_answer", "text": "I don't know"}))
        got, _ = recv_spoken(ws)
        assert "fine not to know" in " ".join(g["text"] for g in got if g["type"] == "teacher_response")
        ws.send_text(json.dumps({"type": "student_answer", "text": "i dont know"}))
        got, _ = recv_spoken(ws)
        assert any("green pigment" in g.get("text", "") for g in got)
        assert next(g for g in got if g["type"] == "quiz_question")["text"] == "What is stomata?"

        # answer while Mab Cat is still speaking (barge-in) → old stream interrupted
        ws.send_text(json.dumps({"type": "student_answer", "text": "tiny openings on the underside of a leaf for air"}))
        got, _ = recv_until(ws, {"lesson_update"})
        assert got[-1]["step"] == "completed" and got[-1]["score"] == "2/3"
        recv_spoken(ws)

        ws.send_text(json.dumps({"type": "session_end"}))
        got, _ = recv_until(ws, {"session_ended"})
        summary = got[-1]["summary"]
        assert summary["answers"] >= 3 and summary["correct"] == 2
    prog = client.get(f"/api/v1/students/{sid}/progress", headers=world["parent"]).json()
    assert any(t["title"] == "Parts of a Leaf" and t["attempts"] >= 2 for t in prog["topics"])
    tr = client.get("/api/v1/sessions/session-001/transcript", headers=world["parent"]).json()
    assert [t["role"] for t in tr["turns"]].count("student") >= 4


def test_ws_free_question_grounded_or_honest(client, world):
    sid, token = world["student"]["id"], world["device"]["device_token"]
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws_auth(ws, token)
        ws.send_text(json.dumps({"type": "session_start", "student_id": sid, "tts": False}))
        recv_until(ws, {"teacher_response"})
        ws.send_text(json.dumps({"type": "student_message", "device_id": "mabcat-001", "student_id": sid,
                                 "session_id": "x", "text": "Can you explain photosynthesis?"}))
        got, _ = recv_until(ws, {"teacher_response"})
        r = got[-1]
        assert "sunlight" in r["text"].lower() and r["sources"] and r["in_curriculum"] is True
        assert r["audio_status"] == "disabled"
        ws.send_text(json.dumps({"type": "student_message", "text": "Tell me about volcanoes"}))
        got, _ = recv_until(ws, {"teacher_response"})
        assert got[-1]["in_curriculum"] is False and "couldn't find" in got[-1]["text"]


def test_ws_llm_mode_cites_sources(client, world, monkeypatch):
    from mabixai.backend.teaching import llm
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: "fake-llm")
    seen = {}

    def fake(messages, retries=1):
        seen["prompt"] = messages[-1]["content"]
        return {"text": "Photosynthesis is how plants make food from sunlight [S1]. നമുക്ക് ഒരു simple example നോക്കാം!",
                "emotion": "excited", "used_sources": ["S1"], "in_curriculum": True}
    monkeypatch.setattr(llm, "chat_json", fake)
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws_auth(ws, world["device"]["device_token"])
        ws.send_text(json.dumps({"type": "session_start", "student_id": world["student"]["id"], "tts": False}))
        recv_until(ws, {"teacher_response"})
        ws.send_text(json.dumps({"type": "student_message", "text": "Photosynthesis എന്താണ്?"}))
        got, _ = recv_until(ws, {"teacher_response"})
    assert got[-1]["engine"] == "llm:fake-llm" and got[-1]["sources"][0]["filename"]
    assert "Malayalam mixed with English" in seen["prompt"] and "[S1]" in seen["prompt"]


def test_robot_commands_and_heartbeat(client, world):
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws_auth(ws, world["device"]["device_token"])
        recv_until(ws, {"robot_expression"})
        ws.send_text(json.dumps({"type": "robot_expression", "expression": "listening", "ear_left": 25, "ear_right": 25}))
        got, _ = recv_until(ws, {"robot_expression"})
        assert got[-1] == {"type": "robot_expression", "expression": "listening", "ear_left": 25, "ear_right": 25, "echo": True}
        ws.send_text(json.dumps({"type": "robot_expression", "expression": "happy", "ear_left": 200}))
        assert recv_until(ws, {"error"})[0][-1]["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "heartbeat"}))
        assert recv_until(ws, {"heartbeat_ack"})[0][-1]["type"] == "heartbeat_ack"


def test_usage_limit(client, world):
    from mabixai.backend.db import session_scope
    from mabixai.backend.models import Student, TeachingSession
    p = client.post("/api/v1/students", headers=world["parent"], json={"display_name": "Ravi", "daily_limit_minutes": 1}).json()
    client.put(f"/api/v1/students/{p['id']}/consent", headers=world["parent"], json={"learning": True, "audio": False})
    with session_scope() as db:
        db.add(TeachingSession(student_id=p["id"], active_seconds=120))
    with client.websocket_connect("/ws/mabcat/mabcat-001") as ws:
        ws_auth(ws, world["device"]["device_token"])
        ws.send_text(json.dumps({"type": "session_start", "student_id": p["id"]}))
        got, _ = recv_until(ws, {"usage_limit_reached", "error"})
        assert any(g.get("code") == "usage_limit_reached" for g in got)


# ------------------------------------------------------------- speech / STT
def espeak(text, voice):
    wav = subprocess.run(["espeak-ng", "-v", voice, "--stdout", text], capture_output=True, check=True).stdout
    return pp.decode_wav(wav)


def speech_stream(sr_out=16000, channels=1):
    """Real speech: English question, pause, Malayalam sentence, pause — as PCM s16le."""
    a, sr = espeak("Mab Cat, what is photosynthesis?", "en-us")
    b, _ = espeak("ഈ ചാപ്റ്റർ എനിക്ക് പറഞ്ഞു തരാമോ", "ml")
    gap = np.zeros(int(sr * 1.0), dtype=np.float32)
    x = np.concatenate([gap[: sr // 2], a, gap, b, gap])
    x = x + np.random.default_rng(1).normal(0, 0.005, len(x)).astype(np.float32)
    x = pp.resample(x, sr, sr_out)
    if channels == 2:
        x = np.stack([x, x * 0.9], axis=1).reshape(-1)
    return pp.float_to_pcm16(x)


@pytest.fixture()
def fake_router():
    from mabixai.backend.speech import router as rmod
    old = rmod.get_router()
    r = rmod.SpeechRouter(providers=[], routes={"*": "test-stt"})
    fake = FakeSTT(["Mab Cat, what is photosynthesis?", "ഈ chapter എനിക്ക് explain ചെയ്യാമോ?"])
    r.register(fake)
    rmod.set_router(r)
    yield fake
    rmod.set_router(old)


def test_audio_ws_stereo_48k_binary_frames_to_teacher(client, world, fake_router):
    """ESP32-style stream: 48 kHz stereo PCM in binary MC frames → final transcripts → AI teacher."""
    token, sid = world["device"]["device_token"], world["student"]["id"]
    pcm = speech_stream(48000, channels=2)
    with client.websocket_connect("/ws/mabcat/mabcat-001") as teach:
        ws_auth(teach, token)
        teach.send_text(json.dumps({"type": "session_start", "student_id": sid, "tts": False}))
        recv_until(teach, {"teacher_response"})
        with client.websocket_connect("/ws/mabcat/audio/mabcat-001") as au:
            ws_auth(au, token)
            au.send_text(json.dumps({"type": "session_start", "device_id": "mabcat-001", "student_id": sid,
                                     "sample_rate": 48000, "channels": 2, "audio_format": "pcm_s16le", "language": "auto"}))
            ready, _ = recv_until(au, {"session_ready"})
            assert ready[-1]["vad"] == "webrtc"
            chunk = 48000 * 2 * 2 // 10  # 100 ms
            seq = 0
            for i in range(0, len(pcm), chunk):
                au.send_bytes(protocol.pack(protocol.KIND_MIC, 7, seq, pcm[i:i + chunk]))
                if seq == 3:  # duplicate/late frame must be ignored
                    au.send_bytes(protocol.pack(protocol.KIND_MIC, 7, seq, pcm[i:i + chunk]))
                seq += 1
            got, _ = recv_until(au, {"final_transcript"})
            types = [g["type"] for g in got]
            assert "speech_detected" in types and "processing_status" in types
            f1 = got[-1]
            assert f1["text"] == "Mab Cat, what is photosynthesis?" and f1["provider"] == "test-stt"
            assert f1["is_final"] and f1["confidence"] is None and f1["language"] == "en"
            assert 1.5 < f1["duration"] < 4.5
            got, _ = recv_until(au, {"final_transcript"})
            f2 = got[-1]
            assert f2["language"] == "ml-en" and f2["code_switching"]["mixed"] is True
            assert f2["utterance_id"] != f1["utterance_id"]
            au.send_text(json.dumps({"type": "session_stop"}))
            stats, _ = recv_until(au, {"session_stopped"})
            assert stats[-1]["utterances"] == 2 and stats[-1]["dropped_chunks"] == 1
        got, _ = recv_until(teach, {"teacher_response"})
        assert any(g["type"] == "student_transcript" and g["provider"] == "test-stt" for g in got)
    assert len(fake_router.calls) == 2


def test_audio_ws_base64_chunks_and_consent(client, world, fake_router):
    token = world["device"]["device_token"]
    p = client.post("/api/v1/students", headers=world["parent"], json={"display_name": "Mia"}).json()
    client.put(f"/api/v1/students/{p['id']}/consent", headers=world["parent"], json={"learning": True, "audio": False})
    with client.websocket_connect("/ws/mabcat/audio/mabcat-001") as au:
        ws_auth(au, token)
        au.send_text(json.dumps({"type": "session_start", "student_id": p["id"], "sample_rate": 16000}))
        assert recv_until(au, {"error"})[0][-1]["code"] == "audio_consent_required"
        au.send_text(json.dumps({"type": "session_start", "student_id": world["student"]["id"], "sample_rate": 16000,
                                 "forward_to_teacher": False}))
        recv_until(au, {"session_ready"})
        pcm = speech_stream(16000)
        for n, i in enumerate(range(0, len(pcm), 3200)):
            au.send_text(json.dumps({"type": "audio_chunk", "sequence": n, "data": base64.b64encode(pcm[i:i + 3200]).decode()}))
        got, _ = recv_until(au, {"final_transcript"})
        assert got[-1]["provider"] == "test-stt"


def test_router_routes_and_records_fallback():
    from mabixai.backend.speech.router import SpeechRouter
    r = SpeechRouter(providers=["faster-whisper", "indicconformer", "openai"], cloud_fallback=False)
    assert [p.name for p, _ in r.candidates("ml")][0] == "indicconformer"
    assert [p.name for p, _ in r.candidates("en")][0] == "faster-whisper"
    assert "indicconformer" not in [p.name for p, _ in r.candidates("auto")]  # needs a language
    assert "openai" not in [p.name for p, _ in r.candidates("ml")]          # cloud only when enabled
    assert "openai" in [p.name for p, _ in SpeechRouter(providers=["faster-whisper", "openai"], cloud_fallback=True).candidates("en")]
    r2 = SpeechRouter(providers=[], routes={"ml": "broken-stt", "*": "test-stt"})
    r2.register(FailingSTT())
    r2.register(FakeSTT(["ok"]))
    t = r2.transcribe(np.zeros(16000, dtype=np.float32), "ml")
    assert t.provider == "test-stt" and t.details["fallback_from"][0]["provider"] == "broken-stt"
    r3 = SpeechRouter(providers=[], routes={"*": "broken-stt"})
    r3.register(FailingSTT())
    with pytest.raises(ProviderUnavailable, match="GPU out of memory"):
        r3.transcribe(np.zeros(16000, dtype=np.float32), "en")


def test_code_switching_examples():
    from mabixai.backend.speech.language import code_switching as c
    assert c.label("Mab Cat, what is photosynthesis?") == "en"
    assert c.label("Photosynthesis എന്താണ്?") == "ml-en"
    assert c.label("ഈ answer explain ചെയ്യൂ.") == "ml-en"
    assert c.label("What is മഴ in English?") == "en-ml"
    assert c.label("गुणा क्या है?") == "hi" and c.label("இது என்ன?") == "ta" and c.label("ಇದು ಏನು?") == "kn"


def test_audio_conversion_formats():
    x = np.sin(np.linspace(0, 2 * np.pi * 440, 48000)).astype(np.float32) * 0.5
    s32 = (x * 2147483647).astype("<i4").tobytes()
    y = pp.to_standard(pp.pcm_to_float(s32, "pcm_s32le", 1), 48000)
    assert len(y) == 16000 and abs(float(np.abs(y).max()) - 0.5) < 0.02
    with pytest.raises(pp.AudioError):
        pp.pcm_to_float(b"\x00" * 8, "mulaw")


def test_stt_rest_file_upload(client, world, fake_router):
    a, sr = espeak("What is multiplication?", "en-us")
    wav = pp.to_wav_bytes(pp.resample(a, sr, 16000))
    r = client.post("/api/v1/stt/transcribe", headers=world["parent"], data={"language": "en"},
                    files={"file": ("q.wav", wav, "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "test-stt" and r.json()["speech_ratio"] > 0.3
    assert client.post("/api/v1/stt/transcribe", headers=world["parent"], files={"file": ("x.wav", b"RIFFjunk", "audio/wav")}).status_code == 422


def test_tts_rest_and_status(client, world):
    r = client.post("/api/v1/tts/synthesize", headers=world["parent"], data={"text": "Hello Anu! നമുക്ക് പഠിക്കാം.", "language": "en"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF" and r.headers["X-TTS-Engine"] == "espeak"
    s = client.get("/api/v1/tts/status", headers=world["parent"]).json()
    assert s["espeak"]["available"] is True


def test_benchmark_metrics_and_run(tmp_path):
    from mabixai.backend.speech import benchmark, router as rmod
    assert benchmark.wer("the cat sat", "the cat sat") == 0
    assert round(benchmark.wer("the cat sat on mat", "the bat sat mat"), 2) == 0.4
    assert benchmark.cer("മഴ", "മഴ") == 0
    manifest = benchmark.make_synthetic(str(tmp_path / "syn"))
    rmod.REGISTRY["test-stt"] = lambda: FakeSTT(["Mab Cat, what is photosynthesis?"] * 10)
    try:
        res = benchmark.run(manifest, ["test-stt"], out_dir=str(tmp_path / "out"))
    finally:
        rmod.REGISTRY.pop("test-stt")
    o = res["providers"]["test-stt"]["overall"]
    assert o["samples"] == 7 and o["failure_rate"] == 0 and o["wer"] is not None
    assert (tmp_path / "out" / "latest.md").read_text().startswith("# STT benchmark")


def test_delete_student_erases_data(client, world):
    p = client.post("/api/v1/students", headers=world["parent"], json={"display_name": "Temp"}).json()
    assert client.delete(f"/api/v1/students/{p['id']}", headers=world["parent"]).status_code == 200
    assert client.get(f"/api/v1/students/{p['id']}/progress", headers=world["parent"]).status_code == 404
    log = client.get("/api/v1/audit", headers=world["admin"]).json()
    assert any(e["action"] == "student.delete" for e in log)
