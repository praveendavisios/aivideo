"""Server-rendered pages. Data is loaded client-side from /api."""
from flask import Blueprint, abort, redirect, render_template, session, url_for

from .. import auth, repo
from ..pipelines import studio

bp = Blueprint("pages", __name__)


@bp.before_request
@auth.login_required
def _guard():
    return None


STUDIO = auth.STUDIO_ROLES


@bp.route("/")
def index():
    return redirect(url_for("pages.home_for_role"))


@bp.route("/home")
def home_for_role():
    """Where each role lands after signing in."""
    role = auth.current_role()
    if role == "teacher" and auth.mabcat_enabled():
        return redirect(url_for("pages.mc_curriculum"))
    if role == "parent":
        return redirect(url_for("pages.mc_students"))
    return redirect(url_for("pages.dashboard"))


def _mabcat_page(template, active, **kw):
    if not auth.mabcat_enabled():
        abort(404)
    return render_template(template, active=active, **kw)


@bp.route("/users")
@auth.role_required("admin")
def mc_users():
    return _mabcat_page("mabcat/users.html", "mc-users")


@bp.route("/students")
@auth.role_required("teacher", "parent")
def mc_students():
    return _mabcat_page("mabcat/students.html", "mc-students")


@bp.route("/centers")
@auth.role_required("admin")
def mc_centers():
    return _mabcat_page("mabcat/centers.html", "mc-centers")


@bp.route("/curriculum")
@auth.role_required("teacher", "parent")
def mc_curriculum():
    return _mabcat_page("mabcat/curriculum.html", "mc-curriculum")


@bp.route("/classes")
@auth.role_required("teacher")
def mc_classes():
    return _mabcat_page("mabcat/classes.html", "mc-classes")


@bp.route("/devices")
@auth.role_required("parent")
def mc_devices():
    return _mabcat_page("mabcat/devices.html", "mc-devices")


@bp.route("/mabcat/console")
@auth.role_required("teacher", "parent")
def mc_console():
    return _mabcat_page("mabcat/console_link.html", "mc-console", target="/console/", title="Teaching console")


@bp.route("/mabcat/speech")
@auth.role_required("teacher", "parent")
def mc_speech():
    return _mabcat_page("mabcat/console_link.html", "mc-speech", target="/console/stt.html", title="Speech console")


@bp.route("/dashboard")
@auth.role_required(*STUDIO)
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@bp.route("/studio/<module>")
@auth.role_required(*STUDIO)
def studio_page(module):
    spec = studio.MODULES.get(module)
    if not spec:
        abort(404)
    return render_template("studio.html", active=f"studio-{module}", module=module, spec=spec,
                           categories=spec.get("categories", {}))


@bp.route("/news-videos")
@auth.role_required(*STUDIO)
def news_page():
    from .api import NEWS_CATEGORIES
    return render_template("news.html", active="news", categories=NEWS_CATEGORIES)


@bp.route("/upload")
@auth.role_required(*STUDIO)
def upload_page():
    from ..config import Config
    return render_template("upload.html", active="upload", max_mb=Config.MAX_UPLOAD_MB,
                           exts=sorted(Config.ALLOWED_VIDEO_EXT))


@bp.route("/library")
@auth.role_required(*STUDIO)
def library_page():
    return render_template("library.html", active="library")


@bp.route("/videos/<int:video_id>")
@auth.role_required(*STUDIO)
def video_page(video_id):
    v = repo.get_video(video_id)
    if not v:
        abort(404)
    return render_template("video.html", active="library", video_id=video_id, video_type=v["video_type"])


@bp.route("/history")
@auth.role_required(*STUDIO)
def history_page():
    return render_template("history.html", active="history")


@bp.route("/settings")
@auth.role_required("admin")
def settings_page():
    return render_template("settings.html", active="settings")
