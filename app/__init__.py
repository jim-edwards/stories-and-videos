"""Stories and Videos: a very simple video library for people who want things to just work."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from flask import Flask, current_app, g, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth
from .config import Config
from .db import Database
from .naming import nice_duration
from .scanner import Scanner

log = logging.getLogger(__name__)


@dataclass
class Services:
    cfg: Config
    db: Database
    scanner: Scanner
    throttle: auth.LoginThrottle


def get_db():
    """The current request's database connection (opened on first use, closed afterwards)."""
    if "db" not in g:
        g.db = current_services().db.connect()
    return g.db


def current_services() -> Services:
    return current_app.extensions["sv"]


def _load_secret_key(cfg: Config) -> bytes:
    path = cfg.data_dir / "secret.key"
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def create_app(config: Config | None = None, *, start_scanner: bool = True) -> Flask:
    cfg = config or Config.from_env()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(cfg.data_dir / "stories.db")
        secret_key = _load_secret_key(cfg)
    except OSError as exc:
        raise SystemExit(
            f"Cannot write to the data folder {cfg.data_dir} ({exc}). "
            "Make sure the folder is mounted and writable by the user the container runs as."
        )

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        MAX_CONTENT_LENGTH=256 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",  # only used for the occasional on-screen message
    )
    if cfg.trust_proxy:
        n = cfg.trust_proxy
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    app.add_template_filter(nice_duration)

    scanner = Scanner(cfg, db)
    app.extensions["sv"] = Services(cfg, db, scanner, auth.LoginThrottle())

    boot = db.connect()
    try:
        auth.bootstrap_admin(boot, cfg, log)
    finally:
        boot.close()

    from . import admin, views
    app.register_blueprint(views.bp)
    app.register_blueprint(admin.bp)

    @app.before_request
    def load_user():
        g.user = None
        g.csrf = None
        g.refresh_token = None
        if request.endpoint in ("static", "views.healthz"):
            return None
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            found = auth.lookup_session(get_db(), token, cfg.session_days)
            if found:
                row, refreshed = found
                g.user, g.csrf = row, row["csrf"]
                g.session_hash = row["token_hash"]
                if refreshed:
                    g.refresh_token = token  # slide the cookie's expiry forward
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and g.user and not auth.csrf_ok():
            if request.path.startswith("/api/"):
                return jsonify(error="please reload the page"), 400
            return render_template("error.html", title="Please try again",
                                   message="That page was out of date. Please go back and try once more."), 400
        return None

    @app.after_request
    def finish(response):
        if g.get("refresh_token"):
            auth.set_session_cookie(response, g.refresh_token, cfg.session_days)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        if request.endpoint != "static" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "private, no-cache"
        return response

    @app.teardown_request
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def template_globals():
        return {"site_name": cfg.site_name, "user": g.get("user"), "csrf_token": g.get("csrf") or ""}

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="not found"), 404
        return render_template("error.html", title="We couldn't find that",
                               message="That page or video isn't here any more."), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", title="Not allowed",
                               message="You don't have permission to see that page."), 403

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", title="Something went wrong",
                               message="Sorry about that. Please try again in a moment."), 500

    if start_scanner:
        scanner.start()
    return app
