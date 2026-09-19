"""Flask web process. Never import or launch the worker here."""

import secrets
from uuid import uuid4

from flask import Flask, request, session, abort

from engine.config import FLASK_SECRET_KEY
from store import get_store


def create_app(test_config: dict | None = None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=FLASK_SECRET_KEY, MAX_CONTENT_LENGTH=16_384,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("FLASK_SECRET_KEY is missing; configure it privately in .env")
    app.extensions["store"] = app.config.get("STORE") or get_store()

    @app.before_request
    def visitor_and_csrf():
        session.setdefault("visitor_id", str(uuid4()))
        session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not secrets.compare_digest(token, session["csrf"]):
                abort(403, description="Your session changed. Reload this page before trying again.")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.teardown_appcontext
    def close_store(error=None):
        app.extensions["store"].close()

    from app.routes import bp
    app.register_blueprint(bp)
    return app
