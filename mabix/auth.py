"""Session authentication, CSRF protection and login throttling."""
import functools
import hmac
import secrets
import time
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import repo
from .config import Config

bp = Blueprint("auth", __name__)

_attempts = {}
MAX_ATTEMPTS, WINDOW = 8, 300


def _password_hash():
    stored = repo.get_setting("auth.password_hash")  # set from Settings → change password
    if stored:
        return stored
    if Config.ADMIN_PASSWORD_HASH:
        return Config.ADMIN_PASSWORD_HASH
    if Config.ADMIN_PASSWORD and not Config.IS_PRODUCTION:
        return generate_password_hash(Config.ADMIN_PASSWORD)
    if not Config.IS_PRODUCTION:
        return generate_password_hash("admin")  # local development default only
    return None


def credentials_source():
    if repo.get_setting("auth.password_hash"):
        return "Password set in Settings"
    if Config.ADMIN_PASSWORD_HASH:
        return "MABIX_ADMIN_PASSWORD_HASH environment variable"
    if Config.ADMIN_PASSWORD:
        return "MABIX_ADMIN_PASSWORD environment variable (development only)"
    return "Development default (admin/admin) — disabled in production"


def verify(username, password):
    pw_hash = _password_hash()
    if not pw_hash:
        return False
    user_ok = hmac.compare_digest(username.encode(), Config.ADMIN_USERNAME.encode())
    return check_password_hash(pw_hash, password) and user_ok


def _throttled(ip):
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
    _attempts[ip] = hits
    return len(hits) >= MAX_ATTEMPTS


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
        if request.path.startswith("/api/") or request.is_json:
            resp = jsonify(error="Security token missing or expired. Reload the page and try again.")
            resp.status_code = 400
            abort(resp)
        abort(400)


def mabcat_enabled():
    """True when the studio runs inside the RoboCat FastAPI app (one login for everything)."""
    import os
    return os.environ.get("MABCAT_MOUNTED") == "1"


def _mabcat_session_user(identifier, password):
    """Authenticate a RoboCat account (email) or map the studio admin to the RoboCat admin.

    Returns {user, name, role, mc_token, mc_user_id} or None.
    """
    if not mabcat_enabled():
        return None
    from mabixai.backend.db import session_scope
    from mabixai.backend.models import User
    from mabixai.backend.security import authenticate, issue_token
    with session_scope() as db:
        u = authenticate(db, identifier, password) if "@" in identifier else None
        if u is None and verify(identifier, password):  # studio admin login → RoboCat admin account
            u = db.query(User).filter(User.role == "admin", User.active.is_(True)).order_by(User.created_at).first()
            if u is None:
                return {"user": identifier, "name": identifier, "role": "admin", "mc_token": None, "mc_user_id": None}
        if u is None:
            return None
        return {"user": u.email, "name": u.name or u.email.split("@")[0], "role": u.role,
                "mc_token": issue_token(db, u), "mc_user_id": u.id}


def is_logged_in():
    return bool(session.get("user"))


def current_role():
    return session.get("role", "admin")


STUDIO_ROLES = ("admin", "teacher")


def role_required(*roles):
    """Allow the given roles (admin always passes)."""
    def deco(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            if current_role() != "admin" and current_role() not in roles:
                if request.path.startswith(("/api/", "/media/")) or request.is_json:
                    return jsonify(error="You don't have access to this section"), 403
                flash("That section is not available for your account.")
                return redirect(url_for("pages.home_for_role"))
            return view(*a, **kw)
        return wrapped
    return deco


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not is_logged_in():
            if (request.path.startswith(("/api/", "/media/")) or request.is_json
                    or request.accept_mimetypes.best == "application/json"):
                return jsonify(error="Authentication required"), 401
            return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))
        return view(*a, **kw)
    return wrapped


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("pages.home_for_role")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("pages.home_for_role"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        check_csrf()
        if _throttled(ip):
            error = "Too many attempts. Please wait a few minutes."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            info = _mabcat_session_user(username, password)
            if info is None and not mabcat_enabled() and verify(username, password):
                info = {"user": username, "name": username, "role": "admin", "mc_token": None, "mc_user_id": None}
            if info:
                session.clear()
                session.permanent = True
                session.update(info)
                csrf_token()
                _attempts.pop(ip, None)
                current_app.logger.info("Login %s (%s) from %s", info["user"], info["role"], ip)
                return redirect(_safe_next(request.form.get("next")))
            _attempts.setdefault(ip, []).append(time.time())
            current_app.logger.warning("Failed login from %s", ip)
            error = "Invalid username or password."
    return render_template("login.html", error=error, next=request.args.get("next", ""),
                           dev_hint=not Config.IS_PRODUCTION and credentials_source().startswith("Development")), \
        (401 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    token = session.get("mc_token")
    if token and mabcat_enabled():
        try:
            from mabixai.backend.db import session_scope
            from mabixai.backend.security import revoke_token
            with session_scope() as db:
                revoke_token(db, token)
        except Exception:
            pass
    session.clear()
    flash("You have been signed out.")
    return redirect(url_for("auth.login"))


def change_password(current, new):
    if not verify(Config.ADMIN_USERNAME, current):
        raise ValueError("Current password is incorrect")
    if len(new) < 10:
        raise ValueError("New password must be at least 10 characters")
    repo.set_setting("auth.password_hash", generate_password_hash(new))


def configure(app):
    app.permanent_session_lifetime = timedelta(hours=Config.SESSION_HOURS)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=Config.IS_PRODUCTION, SESSION_COOKIE_NAME="mabix_session")
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _csrf_guard():
        if request.endpoint in ("auth.login", "static"):
            return
        if is_logged_in():
            check_csrf()
