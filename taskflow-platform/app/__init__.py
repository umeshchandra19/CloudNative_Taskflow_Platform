from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from .cache import TTLCache, Versions
from .config import Config
from .db import Database, close_db, get_db
from .errors import register_error_handlers
from .events import EventBus
from .schema import init_schema

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(overrides=None):
    cfg = Config(**(overrides or {}))
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
    app.config["CFG"] = cfg
    app.json.sort_keys = False

    database = Database(cfg.database_url, cfg.db_pool_max)
    versions = Versions()
    app.extensions.update(
        db=database, versions=versions,
        cache=TTLCache(cfg.cache_max_entries, cfg.cache_ttl),
        bus=EventBus(database, versions, cfg.event_poll_interval, cfg.event_retention_hours))

    if cfg.auto_init_db:
        init_schema(database)

    register_error_handlers(app)
    app.teardown_appcontext(close_db)

    from .routes import admin, auth, dashboard, projects, stream, tasks
    for module in (auth, projects, tasks, dashboard, stream, admin):
        app.register_blueprint(module.bp)

    @app.get("/healthz")
    def healthz():
        get_db().scalar("SELECT 1")
        return jsonify({"status": "ok"})

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        return resp

    if cfg.start_event_poller:
        app.extensions["bus"].start()
    return app
