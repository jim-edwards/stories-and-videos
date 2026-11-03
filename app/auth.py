"""Accounts, passwords, and long-lived sign-in sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from functools import wraps
from urllib.parse import urlparse

from flask import abort, g, jsonify, redirect, request, url_for

SESSION_COOKIE = "sv_session"
MIN_PASSWORD = 8
MAX_PASSWORD = 256
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_REFRESH_AFTER = 6 * 3600  # re-stamp a session (and its cookie) at most this often


class UserError(ValueError):
    """A problem with user input whose message is safe and helpful to show on screen."""


# -- passwords ------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=2**26, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_raw = base64.b64decode(expected)
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected_raw), maxmem=2**26,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected_raw)


# Checked when the username doesn't exist so a wrong name takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_hex(8))


# -- users ----------------------------------------------------------------------------------


def _clean_username(username: str) -> str:
    username = " ".join(username.split())
    if not username:
        raise UserError("Please type a name.")
    if len(username) > 40 or any(ord(c) < 32 for c in username):
        raise UserError("That name is too long (40 letters at most).")
    return username


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"The password needs at least {MIN_PASSWORD} characters. A few easy words work well, like 'sunny garden walk'.")
    if len(password) > MAX_PASSWORD:
        raise UserError("That password is too long.")


def create_user(conn: sqlite3.Connection, username: str, password: str,
                display_name: str = "", is_admin: bool = False) -> int:
    username = _clean_username(username)
    display_name = " ".join(display_name.split())[:60] or username
    _check_password(password)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, display_name, hash_password(password), int(is_admin), time.time()),
        )
    except sqlite3.IntegrityError:
        raise UserError(f"Someone is already using the name '{username}'.")
    return cur.lastrowid


def set_password(conn: sqlite3.Connection, user_id: int, password: str, keep_session: str | None = None) -> None:
    """Change a password and sign the person out everywhere (except `keep_session`)."""
    _check_password(password)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep_session or ""))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))  # sessions/progress cascade


def find_user(conn: sqlite3.Connection, username: str):
    return conn.execute("SELECT * FROM users WHERE username = ?", (" ".join(username.split()),)).fetchone()


def bootstrap_admin(conn: sqlite3.Connection, cfg, log) -> None:
    """Make sure someone can sign in on a brand-new install."""
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    password = cfg.admin_password
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(9)
    create_user(conn, cfg.admin_username, password, "Administrator", is_admin=True)
    if generated:
        log.warning("=" * 60)
        log.warning("First start: created the administrator account.")
        log.warning("   Name:     %s", cfg.admin_username)
        log.warning("   Password: %s", password)
        log.warning("Sign in and add people on the 'Manage' page. This message is shown only once.")
        log.warning("=" * 60)
    else:
        log.info("First start: created the administrator account '%s'.", cfg.admin_username)


# -- sessions -------------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, max_age_days: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE last_seen < ?", (now - max_age_days * 86400,))  # tidy up abandoned ones
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
        (_hash_token(token), user_id, secrets.token_urlsafe(24), now, now),
    )
    return token


def end_session(conn: sqlite3.Connection, token_hash: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def lookup_session(conn: sqlite3.Connection, token: str, max_age_days: int):
    """Return (row, refreshed) for a valid cookie, else None. `row` joins the session and its user."""
    token_hash = _hash_token(token)
    now = time.time()
    row = conn.execute(
        """SELECT s.token_hash, s.csrf, s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ?""",
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    if now - row["last_seen"] > max_age_days * 86400:
        end_session(conn, token_hash)
        return None
    refresh = now - row["last_seen"] > _REFRESH_AFTER
    if refresh:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
    return row, refresh


def set_session_cookie(response, token: str, days: int) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=days * 86400, httponly=True, samesite="Lax",
        secure=request.is_secure, path="/",
    )


# -- brute-force protection -----------------------------------------------------------------


class LoginThrottle:
    """Remember recent failed sign-ins; refuse further tries for a while."""

    WINDOW = 600
    PER_NAME = 8
    PER_ADDRESS = 30

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, address: str, username: str) -> bool:
        now = time.time()
        with self._lock:
            return (len(self._recent(f"n:{address}:{username.lower()}", now)) >= self.PER_NAME
                    or len(self._recent(f"a:{address}", now)) >= self.PER_ADDRESS)

    def record_failure(self, address: str, username: str) -> None:
        now = time.time()
        with self._lock:
            for key in (f"n:{address}:{username.lower()}", f"a:{address}"):
                self._fails.setdefault(key, []).append(now)

    def clear(self, address: str, username: str) -> None:
        with self._lock:
            self._fails.pop(f"n:{address}:{username.lower()}", None)


# -- request helpers ------------------------------------------------------------------------


def safe_next(target: str | None) -> str:
    """Only allow redirects back to a path on this site."""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc:
            return target
    return url_for("views.home")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="signed out"), 401
            return redirect(url_for("views.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def csrf_ok() -> bool:
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return bool(g.csrf) and hmac.compare_digest(sent.encode(), g.csrf.encode())
