"""One login for the whole Mabix app: role landing pages, menu visibility, access control."""
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from mabixai.backend.main import create_app
    d = tmp_path_factory.mktemp("uni")
    return TestClient(create_app(mount_studio=True, database_url=f"sqlite:///{d}/m.db"))


def api_login(c, email, pw):
    return {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["token"]}


def web_login(c, user, pw):
    c.cookies.clear()
    page = c.get("/login").text
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    return c.post("/login", data={"username": user, "password": pw, "_csrf": tok}, follow_redirects=False)


@pytest.fixture(scope="module")
def people(client):
    admin = api_login(client, "admin@mabix.local", "admin")
    center = client.post("/api/v1/centers", headers=admin, json={"name": "Kochi Center", "location": "Kochi"}).json()
    for email, role in (("t@school.in", "teacher"), ("p@home.in", "parent")):
        r = client.post("/api/v1/users", headers=admin, json={"email": email, "role": role, "password": "secret-pass",
                                                              "center_id": center["id"]})
        assert r.status_code == 200, r.text
    return {"admin": admin, "center": center}


def test_landing_pages_per_role(client, people):
    r = web_login(client, "admin", "admin")  # studio admin login maps to the RoboCat admin
    assert r.status_code == 302 and client.get(r.headers["location"]).status_code in (200, 302)
    assert client.get("/home", follow_redirects=False).headers["location"].endswith("/dashboard")
    assert web_login(client, "t@school.in", "secret-pass").status_code == 302
    assert client.get("/home", follow_redirects=False).headers["location"].endswith("/curriculum")
    assert web_login(client, "p@home.in", "secret-pass").status_code == 302
    assert client.get("/home", follow_redirects=False).headers["location"].endswith("/students")
    assert web_login(client, "p@home.in", "wrong").status_code == 401


def test_admin_sees_every_section(client, people):
    web_login(client, "admin@mabix.local", "admin")
    html = client.get("/dashboard").text
    for label in ("Users", "Students", "Centers", "AI Video Studio", "Video Library", "Curricula & lessons",
                  "Classes", "Teaching console", "Robot devices", "Settings"):
        assert label in html, label
    assert 'name="mc-token" content="' in html and 'content=""' not in html.split('name="mc-token"')[1][:12]
    for path in ("/users", "/students", "/centers", "/curriculum", "/classes", "/devices", "/mabcat/console",
                 "/mabcat/speech", "/library", "/studio/short", "/settings"):
        assert client.get(path).status_code == 200, path


def test_teacher_menu_and_access(client, people):
    web_login(client, "t@school.in", "secret-pass")
    html = client.get("/curriculum").text
    assert "Curricula & lessons" in html and "AI Video Studio" in html
    assert 'href="/users"' not in html and 'href="/centers"' not in html and 'href="/settings"' not in html
    assert client.get("/users", follow_redirects=False).status_code == 302
    assert client.get("/library").status_code == 200  # teachers can make videos
    assert client.get("/api/stats").status_code == 200


def test_parent_menu_and_access(client, people):
    web_login(client, "p@home.in", "secret-pass")
    html = client.get("/students").text
    assert "My children" in html and "Robot devices" in html
    assert "AI Video Studio" not in html and 'href="/users"' not in html
    assert client.get("/dashboard", follow_redirects=False).status_code == 302
    assert client.get("/api/stats", headers={"Accept": "application/json"}).status_code == 403
    assert client.get("/classes", follow_redirects=False).status_code == 302


def test_admin_user_center_student_management(client, people):
    admin, center = people["admin"], people["center"]
    users = client.get("/api/v1/users", headers=admin).json()
    parent = next(u for u in users if u["email"] == "p@home.in")
    teacher = next(u for u in users if u["email"] == "t@school.in")
    assert parent["center_id"] == center["id"]
    st = client.post("/api/v1/students", headers=admin, json={"display_name": "Anu", "grade": "5",
                                                             "center_id": center["id"], "parent_id": parent["id"]}).json()
    assert st["parent_id"] == parent["id"] and st["center_id"] == center["id"]
    # the parent sees the child; the teacher sees it through the center
    pa = api_login(client, "p@home.in", "secret-pass")
    te = api_login(client, "t@school.in", "secret-pass")
    assert [s["id"] for s in client.get("/api/v1/students", headers=pa).json()] == [st["id"]]
    assert st["id"] in [s["id"] for s in client.get("/api/v1/students", headers=te).json()]
    c = client.get("/api/v1/centers", headers=admin).json()[0]
    assert c["teachers"] == 1 and c["students"] == 1
    # deactivate + reactivate teacher, reset password
    assert client.patch(f"/api/v1/users/{teacher['id']}", headers=admin, json={"active": False}).json()["active"] is False
    assert client.post("/api/v1/auth/login", json={"email": "t@school.in", "password": "secret-pass"}).status_code == 401
    client.patch(f"/api/v1/users/{teacher['id']}", headers=admin, json={"active": True, "password": "new-secret"})
    assert client.post("/api/v1/auth/login", json={"email": "t@school.in", "password": "new-secret"}).status_code == 200
    assert client.delete(f"/api/v1/centers/{center['id']}", headers=admin).status_code == 409  # still in use
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"active": False}).status_code == 400
    # non-admins cannot manage users or centers
    assert client.post("/api/v1/centers", headers=te, json={"name": "X center"}).status_code == 403
    assert client.patch(f"/api/v1/users/{parent['id']}", headers=te, json={"role": "admin"}).status_code == 403


def test_column_migration_on_old_database(tmp_path):
    """A database created before centers existed gets the new columns automatically."""
    import sqlite3
    from mabixai.backend import db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, email VARCHAR(200), name VARCHAR(200), role VARCHAR(20), "
                "password_hash VARCHAR(300), active BOOLEAN, created_at DATETIME, updated_at DATETIME)")
    con.commit()
    con.close()
    previous = db.engine().url.render_as_string(hide_password=False)
    try:
        db.configure(f"sqlite:///{path}")
        cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(users)")]
        assert "center_id" in cols
    finally:
        db.configure(previous)  # don't leak the throwaway database into later tests


def test_admin_registered_device_can_teach_admin_managed_student(client, people):
    """Regression: device owned by the admin, student belongs to a parent account."""
    import json
    admin = people["admin"]
    users = client.get("/api/v1/users", headers=admin).json()
    assert isinstance(users, list), users
    parent = next(u for u in users if u["role"] == "parent")
    st = client.post("/api/v1/students", headers=admin, json={"display_name": "Kiran", "parent_id": parent["id"]}).json()
    client.put(f"/api/v1/students/{st['id']}/consent", headers=admin, json={"learning": True, "audio": False})
    dev = client.post("/api/v1/devices", headers=admin, json={"device_id": "admin-cat"}).json()
    with client.websocket_connect("/ws/mabcat/admin-cat") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": dev["device_token"]}))
        assert json.loads(ws.receive_text())["type"] == "connected"
        ws.send_text(json.dumps({"type": "session_start", "student_id": st["id"], "tts": False}))
        types = []
        for _ in range(10):
            m = json.loads(ws.receive_text())
            types.append(m["type"])
            if m["type"] in ("session_started", "error"):
                break
        assert types[-1] == "session_started", types
