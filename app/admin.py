"""The 'Manage' page: add people, choose what each can see and how much a day, and look for new videos."""

from __future__ import annotations

import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import auth, current_services, get_db, library, release

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _ago(timestamp: float | None) -> str:
    if not timestamp:
        return "never"
    seconds = int(time.time() - timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


@bp.get("")
@auth.admin_required
def index():
    conn = get_db()
    sv = current_services()
    users = conn.execute(
        """SELECT u.id, u.username, u.display_name, u.is_admin, u.daily_minutes, u.subscribe_all,
                  (SELECT COUNT(*) FROM progress p JOIN videos v ON v.id = p.video_id
                    WHERE p.user_id = u.id AND p.watched = 1 AND v.missing = 0) AS seen_count,
                  (SELECT COUNT(*) FROM subscriptions s WHERE s.user_id = u.id) AS story_count
           FROM users u ORDER BY u.username COLLATE NOCASE"""
    ).fetchall()
    totals = conn.execute(
        "SELECT COUNT(*) AS videos, COUNT(DISTINCT story) AS stories, COUNT(thumb) AS pictures "
        "FROM videos WHERE missing = 0"
    ).fetchone()
    missing = conn.execute("SELECT COUNT(*) FROM videos WHERE missing = 1").fetchone()[0]
    return render_template(
        "admin.html", users=users, totals=totals, missing=missing, scan=sv.scanner.status,
        scan_ago=_ago(sv.scanner.status.finished_at), video_dir=sv.cfg.video_dir,
        scan_interval_min=max(1, sv.cfg.scan_interval // 60),
        release_time=release.get_release_time(conn), timezone_name=sv.cfg.timezone_name,
    )


@bp.post("/users")
@auth.admin_required
def add_user():
    try:
        auth.create_user(
            get_db(), request.form.get("username", ""), request.form.get("password", ""),
            request.form.get("display_name", ""), is_admin=bool(request.form.get("is_admin")),
        )
    except auth.UserError as exc:
        flash(str(exc), "error")
    else:
        flash("Added. They can sign in now.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/users/<int:user_id>/password")
@auth.admin_required
def reset_password(user_id: int):
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        abort(404)
    try:
        keep = g.session_hash if user_id == g.user["id"] else None
        auth.set_password(conn, user_id, request.form.get("password", ""), keep_session=keep)
    except auth.UserError as exc:
        flash(str(exc), "error")
    else:
        flash("Password changed.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/users/<int:user_id>/delete")
@auth.admin_required
def delete_user(user_id: int):
    if user_id == g.user["id"]:
        flash("You can't remove the account you are signed in with.", "error")
    else:
        auth.delete_user(get_db(), user_id)
        flash("Removed.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/rescan")
@auth.admin_required
def rescan():
    current_services().scanner.request_scan()
    flash("Looking for new videos now. Refresh this page in a few seconds to see the result.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/release-time")
@auth.admin_required
def set_release_time():
    try:
        release.set_release_time(get_db(), request.form.get("release_time", ""))
    except auth.UserError as exc:
        flash(str(exc), "error")
    else:
        flash("Saved. New videos will be released at that time each day.", "ok")
    return redirect(url_for("admin.index"))


# -- one person's settings: how much a day, and which stories -------------------------------


def _person_or_404(user_id: int):
    person = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if person is None:
        abort(404)
    return person


@bp.get("/users/<int:user_id>")
@auth.admin_required
def person(user_id: int):
    conn = get_db()
    sv = current_services()
    person = _person_or_404(user_id)
    summary = release.summarize(conn, user_id, sv.cfg.timezone)
    return render_template(
        "admin_person.html", person=person, summary=summary, choices=library.story_choices(conn),
        chosen=library.subscribed_stories(conn, user_id), release_time=release.get_release_time(conn),
        timezone_name=sv.cfg.timezone_name, max_minutes=release.MAX_DAILY_MINUTES,
    )


@bp.post("/users/<int:user_id>/daily")
@auth.admin_required
def set_daily(user_id: int):
    conn = get_db()
    _person_or_404(user_id)
    try:
        if request.form.get("mode") == "daily":
            text = request.form.get("minutes", "").strip()
            if not text.isdecimal() or len(text) > 6:
                raise auth.UserError("Please type the number of minutes, for example 30.")
            release.set_daily_minutes(conn, user_id, int(text))
        else:
            release.set_daily_minutes(conn, user_id, None)
    except auth.UserError as exc:
        flash(str(exc), "error")
    else:
        release.ensure_released(conn, user_id, current_services().cfg.timezone)
        flash("Saved.", "ok")
    return redirect(url_for("admin.person", user_id=user_id))


@bp.post("/users/<int:user_id>/stories")
@auth.admin_required
def set_stories(user_id: int):
    conn = get_db()
    _person_or_404(user_id)
    if request.form.get("mode") == "some":
        library.set_subscriptions(conn, user_id, set(request.form.getlist("story")))
    else:
        library.set_subscriptions(conn, user_id, None)
    release.ensure_released(conn, user_id, current_services().cfg.timezone)
    flash("Saved.", "ok")
    return redirect(url_for("admin.person", user_id=user_id))
