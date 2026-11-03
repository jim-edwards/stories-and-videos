"""The 'Manage' page: add people, reset passwords, and look for new videos."""

from __future__ import annotations

import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import auth, current_services, get_db

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
        """SELECT u.id, u.username, u.display_name, u.is_admin,
                  (SELECT COUNT(*) FROM progress p JOIN videos v ON v.id = p.video_id
                    WHERE p.user_id = u.id AND p.watched = 1 AND v.missing = 0) AS seen_count
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
