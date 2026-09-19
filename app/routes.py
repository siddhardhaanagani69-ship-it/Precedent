"""Pages and small JSON responses; no external services in web requests."""

import re
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from engine.config import DB_BACKEND

bp = Blueprint("web", __name__)


def store():
    return current_app.extensions["store"]


def accessible(council_id: str, owner: bool = False) -> dict:
    council = store().get_council(council_id)
    if not council or (council["visitor_id"] != session["visitor_id"] and (owner or not council["is_featured"])):
        abort(404)
    return council


@bp.get("/")
def index():
    return render_template("index.html", featured=store().list_featured(), profile=store().get_profile(session["visitor_id"]), error=None)


@bp.post("/councils")
def create_council():
    try:
        council = store().create_council(session["visitor_id"], request.form.get("question", ""))
    except ValueError as exc:
        return render_template("index.html", featured=store().list_featured(), profile=store().get_profile(session["visitor_id"]), error=str(exc)), 400
    return redirect(url_for("web.room", council_id=council["id"]))


@bp.get("/c/<council_id>")
def room(council_id):
    return render_template("room.html", council=accessible(council_id))


@bp.get("/api/councils/<council_id>/state")
def state(council_id):
    council = accessible(council_id)
    safe_council = {k: council[k] for k in ("id", "question", "status", "error", "progress", "created_at", "finished_at")}
    safe_council["plan"] = {k: council["plan"].get(k) for k in ("title", "options")}
    return jsonify(council=safe_council, agents=store().get_agents(council_id),
                   turns=store().get_turns(council_id), evidence=store().get_evidence(council_id),
                   question=({k: question[k] for k in ('id', 'text', 'why', 'answers')}
                             if (question := store().get_question(council_id)) else None),
                   can_answer=council['visitor_id'] == session['visitor_id'], verdict=store().get_verdict(council_id))


@bp.get("/api/councils/<council_id>/cite/<label>")
def cite(council_id, label):
    accessible(council_id)
    if not re.fullmatch(r"[SE]\d+", label):
        abort(404)
    rows = store().get_stories(council_id) if label.startswith("S") else store().get_evidence(council_id)
    row = next((r for r in rows if r["label"] == label), None)
    if not row:
        abort(404)
    return jsonify(label=label, summary=row.get("summary") or row.get("body"), url=row.get("url"),
                   source=row.get("source") or row.get("kind"))


@bp.post("/api/councils/<council_id>/answer")
def answer(council_id):
    accessible(council_id, owner=True)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("answer_id"), str):
        return jsonify(error="Choose one of the provided answers."), 400
    try:
        store().insert_answer(council_id, session["visitor_id"], payload["answer_id"])
    except (ValueError, PermissionError):
        return jsonify(error="This council is not awaiting that answer."), 409
    return jsonify(ok=True)


@bp.post("/api/councils/<council_id>/rerun")
def rerun(council_id):
    original = accessible(council_id)
    council = store().create_council(session["visitor_id"], original["question"])
    return jsonify(url=url_for("web.room", council_id=council["id"])), 201


@bp.get("/api/health")
def health():
    beat = store().get_heartbeat()
    age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(beat)).total_seconds()) if beat else None
    return jsonify(backend=DB_BACKEND, worker_heartbeat_age=age, worker_online=age is not None and age <= 15)


@bp.app_errorhandler(404)
@bp.app_errorhandler(403)
@bp.app_errorhandler(413)
def request_error(error):
    messages = {404: "That council is unavailable in this session.",
                403: "Your session changed. Reload the page and try again.",
                413: "That request is too large. Keep your decision under 2,000 characters."}
    if request.path.startswith("/api/"):
        return jsonify(error=messages[error.code]), error.code
    return render_template("error.html", message=messages[error.code]), error.code
