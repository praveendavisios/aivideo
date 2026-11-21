"""Server-rendered pages. Data is loaded client-side from /api."""
from flask import Blueprint, abort, redirect, render_template, url_for

from .. import auth, repo
from ..pipelines import studio

bp = Blueprint("pages", __name__)


@bp.before_request
@auth.login_required
def _guard():
    return None


@bp.route("/")
def index():
    return redirect(url_for("pages.dashboard"))


@bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@bp.route("/studio/<module>")
def studio_page(module):
    spec = studio.MODULES.get(module)
    if not spec:
        abort(404)
    return render_template("studio.html", active=f"studio-{module}", module=module, spec=spec,
                           categories=spec.get("categories", {}))


@bp.route("/news-videos")
def news_page():
    from .api import NEWS_CATEGORIES
    return render_template("news.html", active="news", categories=NEWS_CATEGORIES)


@bp.route("/upload")
def upload_page():
    from ..config import Config
    return render_template("upload.html", active="upload", max_mb=Config.MAX_UPLOAD_MB,
                           exts=sorted(Config.ALLOWED_VIDEO_EXT))


@bp.route("/library")
def library_page():
    return render_template("library.html", active="library")


@bp.route("/videos/<int:video_id>")
def video_page(video_id):
    v = repo.get_video(video_id)
    if not v:
        abort(404)
    return render_template("video.html", active="library", video_id=video_id, video_type=v["video_type"])


@bp.route("/history")
def history_page():
    return render_template("history.html", active="history")


@bp.route("/settings")
def settings_page():
    return render_template("settings.html", active="settings")
