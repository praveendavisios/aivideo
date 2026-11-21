"""End-to-end tests for the Mabix web app, job queue and renderer.

GPU models, Ollama and Google TTS are replaced with small fakes; everything
else (Flask routes, SQLite, job worker, moviepy rendering, probing,
thumbnails) runs for real.
"""
import io
import os
import re
import wave

import numpy as np
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MABIX_DISABLE_WORKER", "1")
    from mabix import config
    data = tmp_path / "data"
    monkeypatch.setattr(config.Config, "DATA_DIR", str(data))
    monkeypatch.setattr(config.Config, "DB_PATH", str(data / "t.db"))
    monkeypatch.setattr(config.Config, "LEGACY_VIDEO_DIR", str(tmp_path / "videos"))
    monkeypatch.setattr(config.Config, "PEXELS_KEY", "secret-pexels-key")
    from mabix import create_app
    os.makedirs(data, exist_ok=True)
    app = create_app({"TESTING": True})
    return app, tmp_path


@pytest.fixture()
def client(env):
    app, _ = env
    return app.test_client()


def csrf(client):
    html = client.get("/dashboard").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client, user="admin", pw="admin"):
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    return client.post("/login", data={"username": user, "password": pw, "_csrf": token})


@pytest.fixture()
def auth(client):
    assert login(client).status_code == 302
    return {"X-CSRF-Token": csrf(client)}


def make_mp4(path, seconds=1.2, size=(320, 240)):
    from moviepy import ColorClip
    ColorClip(size, (30, 120, 200), duration=seconds).write_videofile(str(path), fps=10, logger=None)
    return path


@pytest.fixture()
def fake_ai(monkeypatch, tmp_path):
    """Replace LLM, GPU models and TTS with deterministic fakes."""
    from mabix.pipelines import llm, models, render

    plan = {"title": "What is AI", "description": "A quick explainer.", "hashtags": ["#ai", "learn"],
            "characters": "",
            "scenes": [{"narration": "AI lets machines learn from data.", "visual": "robot reading books",
                        "heading": "Definition", "keywords": ["data"]},
                       {"narration": "It powers assistants and self driving cars.", "visual": "car on highway",
                        "heading": "Uses", "keywords": ["cars"]}]}
    calls = {"clips": 0}
    monkeypatch.setattr(llm, "chat_json", lambda prompt, retries=2: plan)

    def clip(prompt, out, negative="", seed=0, aspect="9:16", duration=None, base_model=None):
        calls["clips"] += 1
        calls.setdefault("log", []).append({"prompt": prompt, "negative": negative, "duration": duration,
                                            "base_model": base_model})
        return str(make_mp4(out, 1.0))
    monkeypatch.setattr(models, "generate_video_clip", clip)
    monkeypatch.setattr(models, "video_engine", lambda: "animatediff")

    def image(prompt, out, **kw):
        from PIL import Image
        Image.new("RGB", (256, 384), (200, 80, 40)).save(out)
        return out
    monkeypatch.setattr(models, "generate_image", image)

    def tts(text, out, voice="en-us", slow=False):
        sr = 16000
        t = np.linspace(0, 1.0, sr)
        with wave.open(out, "w") as f:  # WAV data in a .mp3-named file: ffmpeg probes content
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes((0.1 * np.sin(2 * np.pi * 330 * t) * 32767).astype(np.int16).tobytes())
        return out
    monkeypatch.setattr(render, "synthesize", tts)
    return calls


def run_jobs():
    from mabix import jobs
    jobs.run_pending_sync()


# ------------------------------------------------------------------ auth
def test_routes_require_login(client):
    for path in ["/dashboard", "/library", "/studio/short", "/history", "/settings", "/upload", "/news-videos", "/classic"]:
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert client.get("/api/stats").status_code == 401
    assert client.post("/generate", json={}).status_code == 401
    assert client.get("/progress").status_code in (302, 401)
    assert client.get("/progress", headers={"Accept": "application/json"}).status_code == 401


def test_login_logout(client):
    assert login(client, pw="wrong").status_code == 401
    assert login(client).status_code == 302
    assert client.get("/dashboard").status_code == 200
    token = csrf(client)
    client.post("/logout", data={"_csrf": token})
    assert client.get("/dashboard").status_code == 302


def test_csrf_enforced(client, auth):
    assert client.post("/api/jobs/1/cancel").status_code == 400
    assert client.post("/api/jobs/999/cancel", headers=auth).status_code == 404


def test_production_rejects_default_credentials(client, monkeypatch):
    from mabix import config
    monkeypatch.setattr(config.Config, "IS_PRODUCTION", True)
    assert login(client).status_code == 401


def test_password_change(client, auth):
    r = client.post("/api/settings/password", json={"current": "admin", "new": "a-much-better-pass"}, headers=auth)
    assert r.status_code == 200
    client.post("/logout", data={"_csrf": auth["X-CSRF-Token"]})
    assert login(client).status_code == 401
    assert login(client, pw="a-much-better-pass").status_code == 302


# ---------------------------------------------------------------- pages
def test_all_pages_render(client, auth):
    for path in ["/dashboard", "/studio/short", "/studio/animation", "/studio/story", "/studio/answer",
                 "/news-videos", "/upload", "/library", "/history", "/settings", "/classic"]:
        r = client.get(path)
        assert r.status_code == 200, path
    assert client.get("/studio/nope").status_code == 404


def test_settings_never_leak_secrets(client, auth):
    body = client.get("/api/settings").get_data(as_text=True)
    assert "secret-pexels-key" not in body
    assert client.get("/api/settings").json["api"]["pexels"] is True


# --------------------------------------------------------------- upload
def test_upload_flow(client, auth, env):
    _, tmp = env
    mp4 = make_mp4(tmp / "clip.mp4")
    with open(mp4, "rb") as fh:
        r = client.post("/api/uploads", headers=auth, content_type="multipart/form-data",
                        data={"file": (fh, "My Clip.mp4"), "title": "Holiday", "tags": "fun", "category": "Vlog"})
    assert r.status_code == 201, r.json
    v = r.json["video"]
    assert v["source_type"] == "upload" and v["status"] == "completed" and v["width"] == 320
    assert "file_path" not in v
    assert client.get(f"/media/thumb/{v['id']}").status_code == 200
    dl = client.get(f"/media/video/{v['id']}?download=1")
    assert dl.status_code == 200 and "attachment" in dl.headers["Content-Disposition"]
    lib = client.get("/api/videos?filter=uploaded").json
    assert lib["total"] == 1
    stats = client.get("/api/stats").json["stats"]
    assert stats["total"] == 1 and stats["uploaded"] == 1 and stats["ai_generated"] == 0
    # metadata edit
    r = client.patch(f"/api/videos/{v['id']}", json={"title": "Renamed"}, headers=auth)
    assert r.json["video"]["title"] == "Renamed"
    # cannot regenerate uploads
    assert client.post(f"/api/videos/{v['id']}/regenerate", headers=auth).status_code == 400
    # delete removes files
    assert client.delete(f"/api/videos/{v['id']}", headers=auth).status_code == 200
    assert client.get("/api/videos").json["total"] == 0
    assert not os.listdir(os.path.join(env[1], "data", "uploads"))


def test_upload_validation(client, auth):
    r = client.post("/api/uploads", headers=auth, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(b"hello"), "notes.txt")})
    assert r.status_code == 415
    r = client.post("/api/uploads", headers=auth, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(b"not really a video"), "fake.mp4")})
    assert r.status_code == 422


# --------------------------------------------------------------- studio
def test_studio_review_and_render(client, auth, fake_ai):
    r = client.post("/api/studio/answer", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "What is Artificial Intelligence?", "aspect": "9:16", "resolution": "720p",
                          "duration": "30", "review": "1", "transition": "crossfade"})
    assert r.status_code == 201, r.json
    vid = r.json["video_id"]
    # duplicate submit within seconds is rejected
    r2 = client.post("/api/studio/answer", headers=auth, content_type="multipart/form-data",
                     data={"prompt": "What is Artificial Intelligence?", "review": "1"})
    assert r2.status_code == 409
    run_jobs()
    v = client.get(f"/api/videos/{vid}").json["video"]
    assert v["status"] == "review" and len(v["script"]["scenes"]) == 2 and v["title"] == "What is AI"
    assert v["config"]["headings"] is True  # short-answer videos show section headings

    # edit: change narration and add a scene
    scenes = [{"narration": s["narration"], "visual": s["visual"], "heading": s["heading"], "keywords": s["keywords"],
               "source_index": i} for i, s in enumerate(v["script"]["scenes"])]
    scenes.append({"narration": "That is AI in a nutshell.", "visual": "lightbulb", "heading": "Summary",
                   "keywords": [], "source_index": None})
    r = client.put(f"/api/videos/{vid}/script", json={"title": "AI explained", "scenes": scenes}, headers=auth)
    assert r.status_code == 200 and len(r.json["video"]["script"]["scenes"]) == 3

    assert client.post(f"/api/videos/{vid}/render", headers=auth).status_code == 200
    assert client.post(f"/api/videos/{vid}/render", headers=auth).status_code == 409  # dedupe
    run_jobs()
    v = client.get(f"/api/videos/{vid}").json["video"]
    assert v["status"] == "completed", v.get("error")
    assert v["width"] == 720 and v["height"] == 1280 and v["duration"] > 3
    assert fake_ai["clips"] == 3
    assert all(s["has_clip"] for s in v["script"]["scenes"])
    assert client.get(f"/media/video/{vid}").status_code == 200
    stats = client.get("/api/stats").json["stats"]
    assert stats["completed"] == 1 and stats["ai_generated"] == 1 and stats["today"] == 1

    # regenerate re-creates every AI clip
    assert client.post(f"/api/videos/{vid}/regenerate", headers=auth).status_code == 200
    run_jobs()
    assert fake_ai["clips"] == 6
    assert client.get(f"/api/videos/{vid}").json["video"]["status"] == "completed"


def test_studio_auto_render_chain(client, auth, fake_ai):
    r = client.post("/api/studio/short", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "Deep ocean facts", "review": "0", "resolution": "720p", "aspect": "1:1"})
    assert r.status_code == 201 and r.json["render_job_id"]
    jobs = client.get("/api/jobs").json["items"]
    assert {j["status"] for j in jobs} == {"queued", "pending"}
    run_jobs()
    v = client.get(f"/api/videos/{r.json['video_id']}").json["video"]
    assert v["status"] == "completed" and v["width"] == v["height"] == 720


def test_clip_failure_falls_back_to_keyframe(client, auth, fake_ai, monkeypatch):
    from mabix.pipelines import models

    def broken(*a, **k):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(models, "generate_video_clip", broken)
    r = client.post("/api/studio/story", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "A lighthouse keeper story", "review": "0", "resolution": "720p", "category": "moral"})
    run_jobs()
    vid = r.json["video_id"]
    v = client.get(f"/api/videos/{vid}").json["video"]
    assert v["status"] == "completed"
    assert all(s["visual_kind"] == "ai-image (camera motion)" for s in v["script"]["scenes"])
    job = [j for j in client.get(f"/api/videos/{vid}").json["jobs"] if j["kind"] == "render"][0]
    log = client.get(f"/api/jobs/{job['id']}").json["job"]["log"]
    assert "CUDA out of memory" in log


def test_llm_unavailable_fails_honestly_and_retries(client, auth, monkeypatch, fake_ai):
    from mabix.pipelines import llm

    def down(prompt, retries=2):
        raise llm.LLMError("Cannot reach Ollama at http://127.0.0.1:11434")
    monkeypatch.setattr(llm, "chat_json", down)
    r = client.post("/api/studio/animation", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "Robot in candy forest", "review": "0"})
    run_jobs()
    vid = r.json["video_id"]
    data = client.get(f"/api/videos/{vid}").json
    assert data["video"]["status"] == "failed" and "Ollama" in data["video"]["error"]
    statuses = sorted(j["status"] for j in data["jobs"])
    assert statuses == ["cancelled", "failed"]  # render never ran
    failed = [j for j in data["jobs"] if j["status"] == "failed"][0]
    monkeypatch.setattr(llm, "chat_json", lambda prompt, retries=2: {
        "title": "Robot", "scenes": [{"narration": "Hello world.", "visual": "robot"}]})
    assert client.post(f"/api/jobs/{failed['id']}/retry", headers=auth).status_code == 200
    run_jobs()
    assert client.get(f"/api/videos/{vid}").json["video"]["status"] == "review"


def test_cancel_queued_job(client, auth, fake_ai):
    r = client.post("/api/studio/short", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "Cancel me please", "review": "1"})
    job_id = r.json["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=auth).json["job"]["status"] == "cancelled"
    run_jobs()
    assert client.get("/api/jobs?status=cancelled").json["total"] == 1


# ------------------------------------------------------- news + legacy
def test_news_quick_mode_uses_original_pipeline(client, auth, monkeypatch, env):
    from mabix.pipelines import legacy
    _, tmp = env
    out = make_mp4(tmp / "videos" / "news.mp4") if os.makedirs(tmp / "videos", exist_ok=True) is None else None
    seen = {}

    def fake_short(news, fmt, ai_only):
        seen.update(news=news, fmt=fmt, ai_only=ai_only)
        return {"news": news, "title": "BREAKING Test", "description": "desc #news", "video": str(out),
                "folder": str(tmp / "videos"), "images": "Pexels → AI"}
    monkeypatch.setattr(legacy, "generate_news_short", fake_short)
    r = client.post("/api/news/generate", json={"articles": ["Mars rover finds water"], "format": "short",
                                                "ai_only": True, "mode": "quick"}, headers=auth)
    assert r.status_code == 201
    run_jobs()
    assert seen == {"news": "Mars rover finds water", "fmt": "short", "ai_only": True}
    v = client.get("/api/videos?filter=news").json["items"][0]
    assert v["status"] == "completed" and v["title"] == "BREAKING Test"

    # legacy endpoints keep working with the classic UI contract
    monkeypatch.setattr(legacy, "generate_news_short", fake_short)
    assert client.post("/generate", json={"news": ["Story A"], "format": "short"}, headers=auth).json == {"status": "started"}
    run_jobs()
    p = client.get("/progress").json
    assert p["status"] == "completed" and p["progress"] == 100 and p["videos"][0]["title"] == "BREAKING Test"
    assert client.get("/video?path=" + p["videos"][0]["video"]).status_code == 200


def test_legacy_video_blocks_path_traversal(client, auth):
    assert client.get("/video?path=/etc/passwd").status_code == 404
    assert client.get("/video?path=../../etc/passwd").status_code == 404
    assert client.get("/open_folder?path=/etc").status_code == 400


def test_publish_requires_youtube_token(client, auth, env):
    _, tmp = env
    with open(make_mp4(tmp / "p.mp4"), "rb") as fh:
        v = client.post("/api/uploads", headers=auth, content_type="multipart/form-data",
                        data={"file": (fh, "p.mp4")}).json["video"]
    r = client.post(f"/api/videos/{v['id']}/publish", json={"privacy": "public"}, headers=auth)
    assert r.status_code == 400 and "YouTube" in r.json["error"]


def test_music_library(client, auth):
    buf = io.BytesIO()
    with wave.open(buf, "w") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000); f.writeframes(b"\x00\x00" * 8000)
    buf.seek(0)
    r = client.post("/api/music", headers=auth, content_type="multipart/form-data", data={"file": (buf, "calm bed.wav")})
    assert r.status_code == 201 and r.json["items"][0]["name"] == "calm_bed.wav"
    assert client.delete("/api/music/calm_bed.wav", headers=auth).json["items"] == []


def test_render_with_music_and_captions(tmp_path, fake_ai):
    """Real moviepy render with a music bed mixed under narration."""
    from mabix.pipelines import render
    clip = make_mp4(tmp_path / "c.mp4", 0.8)
    music = tmp_path / "m.wav"
    with wave.open(str(music), "w") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b"\x01\x00" * 16000)
    out = tmp_path / "o.mp4"
    render.render([{"narration": "One two three four five six seven eight nine.", "clip": str(clip), "keywords": ["five"]},
                   {"narration": "Second scene.", "clip": str(clip), "heading": "Part 2"}],
                  {"aspect": "16:9", "resolution": "720p", "transition": "fade", "headings": True,
                   "music_path": str(music), "fps": 12}, str(tmp_path), str(out))
    from mabix.media import probe
    info = probe(str(out))
    assert (info["width"], info["height"]) == (1280, 720) and info["duration"] > 2


def test_anime_style_uses_stylized_checkpoint_and_narration_length(client, auth, fake_ai):
    r = client.post("/api/studio/short", headers=auth, content_type="multipart/form-data",
                    data={"prompt": "A samurai cat at sunset", "style": "anime", "review": "0", "resolution": "720p"})
    run_jobs()
    assert client.get(f"/api/videos/{r.json['video_id']}").json["video"]["status"] == "completed"
    call = fake_ai["log"][0]
    assert call["base_model"] == "Lykon/dreamshaper-8"          # not the photoreal checkpoint
    assert "photorealistic" in call["negative"]                 # pushed away from photo look
    assert "anime key visual" in call["prompt"]
    assert call["duration"] and call["duration"] > 1            # clip sized to its narration


def test_realistic_style_keeps_default_checkpoint(client, auth, fake_ai):
    client.post("/api/studio/short", headers=auth, content_type="multipart/form-data",
                data={"prompt": "Ocean documentary", "style": "realistic", "review": "0", "resolution": "720p"})
    run_jobs()
    call = fake_ai["log"][0]
    assert call["base_model"] is None and "anime" in call["negative"]


def test_wan_frame_counts():
    from mabix.pipelines.models import wan_frames
    assert wan_frames(5, 24, 121) == 121
    assert wan_frames(3, 24, 121) == 73 and (73 - 1) % 4 == 0
    assert wan_frames(30, 16, 81) == 81
    assert wan_frames(0.5, 16, 81) == 33


def test_quality_and_engine_settings(client, auth):
    r = client.put("/api/settings/models", json={"video_engine": "wan22", "quality": "high",
                                                  "stylized": "someone/anime-model"}, headers=auth)
    assert r.status_code == 200
    m = r.json["models"]
    assert m["video_engine"] == "wan22" and m["quality"] == "high" and m["stylized"] == "someone/anime-model"
    assert client.put("/api/settings/models", json={"quality": "ultra"}, headers=auth).status_code == 400
