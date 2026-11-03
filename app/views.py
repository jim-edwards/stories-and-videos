"""Pages for the people who watch: sign in, the story list, and the player."""

from __future__ import annotations

import math

from flask import (Blueprint, abort, g, jsonify, make_response, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.security import safe_join

from . import auth, current_services, get_db, library
from .naming import story_title

bp = Blueprint("views", __name__)

MIME_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4",
    ".webm": "video/webm", ".ogv": "video/ogg", ".mkv": "video/x-matroska",
}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
SHOW_AT_FIRST = 3  # unseen videos listed per story before "Show more"


@bp.get("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return "ok", 200, {"Content-Type": "text/plain"}


# -- signing in -----------------------------------------------------------------------------


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("views.home"))
    sv = current_services()
    error, username = None, ""
    next_url = request.values.get("next", "")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        address = request.remote_addr or "unknown"
        if sv.throttle.blocked(address, username):
            error = "Too many tries. Please wait a few minutes, then try again."
            return render_template("login.html", error=error, username=username, next_url=next_url), 429

        user = auth.find_user(get_db(), username) if len(password) <= auth.MAX_PASSWORD else None
        good = auth.verify_password(password, user["password_hash"] if user else auth.DUMMY_HASH)
        if user and good:
            sv.throttle.clear(address, username)
            token = auth.create_session(get_db(), user["id"], sv.cfg.session_days)
            response = make_response(redirect(auth.safe_next(next_url)))
            auth.set_session_cookie(response, token, sv.cfg.session_days)
            return response
        sv.throttle.record_failure(address, username)
        error = "That name or password didn't work. Please check it and try again."

    return render_template("login.html", error=error, username=username, next_url=next_url)


@bp.post("/logout")
def logout():
    if g.user:
        auth.end_session(get_db(), g.session_hash)
    response = make_response(redirect(url_for("views.login")))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# -- the story list -------------------------------------------------------------------------


@bp.get("/")
@auth.login_required
def home():
    stories = library.load_stories(get_db(), g.user["id"])
    return render_template(
        "home.html",
        stories=stories,
        show_at_first=SHOW_AT_FIRST,
        open_story=request.args.get("open", type=int),
    )


@bp.post("/video/<int:video_id>/unseen")
@auth.login_required
def mark_unseen(video_id: int):
    library.mark_unseen(get_db(), g.user["id"], video_id)
    # Come back to the same "Already seen" list, still open.
    stories = library.load_stories(get_db(), g.user["id"])
    index = next((i for i, s in enumerate(stories, 1) if any(v.id == video_id for v in s.videos)), None)
    if index is None:
        return redirect(url_for("views.home"))
    return redirect(url_for("views.home", open=index) + f"#story-{index}")


# -- watching -------------------------------------------------------------------------------


@bp.get("/watch/<int:video_id>")
@auth.login_required
def watch(video_id: int):
    conn = get_db()
    video = library.get_video(conn, g.user["id"], video_id)
    if video is None:
        abort(404)
    siblings = library.story_videos(conn, g.user["id"], video.story)
    return render_template(
        "watch.html",
        video=video,
        next_video=library.pick_next(siblings, video.id),
        story_title=story_title(video.story),
        mime=MIME_TYPES.get("." + video.rel_path.rsplit(".", 1)[-1].lower(), "video/mp4"),
    )


def _send_library_file(rel_path: str, mimetypes: dict[str, str], default: str, **options):
    path = safe_join(str(current_services().cfg.video_dir), rel_path)
    if path is None:
        abort(404)
    ext = "." + rel_path.rsplit(".", 1)[-1].lower()
    try:
        # conditional=True gives us HTTP Range support, which is what makes seeking work.
        response = send_file(path, mimetype=mimetypes.get(ext, default), conditional=True, **options)
    except (FileNotFoundError, PermissionError):
        abort(404)
    response.cache_control.private = True
    return response


@bp.get("/media/<int:video_id>")
@auth.login_required
def media(video_id: int):
    row = get_db().execute("SELECT rel_path FROM videos WHERE id = ? AND missing = 0", (video_id,)).fetchone()
    if row is None:
        abort(404)
    return _send_library_file(row["rel_path"], MIME_TYPES, "video/mp4")


@bp.get("/thumb/<int:video_id>")
@auth.login_required
def thumb(video_id: int):
    row = get_db().execute("SELECT thumb FROM videos WHERE id = ? AND missing = 0", (video_id,)).fetchone()
    if row is None or not row["thumb"]:
        abort(404)
    return _send_library_file(row["thumb"], IMAGE_TYPES, "image/jpeg", max_age=300)


@bp.post("/api/progress/<int:video_id>")
@auth.login_required
def save_progress(video_id: int):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    try:
        position = float(data.get("position") or 0)
        duration = float(data.get("duration") or 0)
    except (TypeError, ValueError):
        abort(400)
    if not (math.isfinite(position) and math.isfinite(duration)):
        abort(400)
    watched = library.record_progress(get_db(), g.user["id"], video_id, position, duration, bool(data.get("ended")))
    if watched is None:
        abort(404)
    return jsonify(watched=watched)
