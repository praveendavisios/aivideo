"""Mabix AI — AI video creation & publishing platform."""
import logging
import os

from flask import Flask, jsonify, render_template, request

from .config import Config

__version__ = "1.0.0"


def create_app(test_config=None):
    from . import auth, db, jobs, storage
    from .routes import api, legacy, pages

    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_MB * 1024 * 1024
    if test_config:
        app.config.update(test_config)
        for k in ("DATA_DIR", "DB_PATH", "LEGACY_VIDEO_DIR"):
            if k in test_config:
                setattr(Config, k, test_config[k])

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("MABIX_SECRET_KEY must be set in production")
    if Config.IS_PRODUCTION and not (Config.ADMIN_PASSWORD_HASH or Config.ADMIN_PASSWORD):
        app.logger.warning("No MABIX_ADMIN_PASSWORD_HASH set: login is disabled until one is configured")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    storage.ensure_dirs()
    db.set_db_path(Config.DB_PATH)
    db.migrate()

    auth.configure(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(api.media_bp)
    app.register_blueprint(legacy.bp)

    @app.context_processor
    def _branding():
        from . import repo
        return {"logo_url": Config.LOGO_URL, "app_version": __version__, "admin_name": Config.ADMIN_USERNAME,
                "theme_pref": (repo.get_setting("preferences", {}) or {}).get("theme", "system"),
                "studio_modules": __import__("mabix.pipelines.studio", fromlist=["MODULES"]).MODULES}

    def _wants_json():
        return request.path.startswith(("/api/", "/media/")) or request.is_json

    @app.errorhandler(404)
    def _404(e):
        if _wants_json():
            return jsonify(error="Not found"), 404
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error=f"File too large (max {Config.MAX_UPLOAD_MB} MB)"), 413

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("Server error")
        if _wants_json():
            return jsonify(error="Internal server error — see server log"), 500
        return render_template("error.html", code=500, message="Something went wrong"), 500

    @app.teardown_appcontext
    def _close(exc):
        pass  # thread-local connections are reused

    reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if not Config.DISABLE_WORKER and not app.config.get("TESTING") and (not app.debug or reloader_child):
        jobs.start_worker()
    return app
