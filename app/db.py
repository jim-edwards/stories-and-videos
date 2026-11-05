"""SQLite storage: users, sign-in sessions, the scanned video list, and watch progress."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL,
    daily_minutes INTEGER,                   -- minutes of new videos released per day; NULL = no limit
    subscribe_all INTEGER NOT NULL DEFAULT 1 -- 1 = every story; 0 = only the ones in `subscriptions`
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf       TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    last_seen  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

-- One row per video file found in the video folder. Rows are never deleted when a
-- file disappears (just flagged `missing`) so watch history survives a NAS hiccup.
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY,
    story       TEXT    NOT NULL,          -- top-level folder name; '' = loose files
    rel_path    TEXT    NOT NULL UNIQUE,   -- path inside the video folder, '/' separated
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    track       INTEGER,                   -- episode/track number from metadata, if any
    thumb       TEXT,                      -- path of the picture saved next to the video, if any
    duration    REAL,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    missing     INTEGER NOT NULL DEFAULT 0,
    added_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    user_id    INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    position   REAL    NOT NULL DEFAULT 0,
    watched    INTEGER NOT NULL DEFAULT 0,
    watched_at REAL,
    updated_at REAL    NOT NULL,
    PRIMARY KEY (user_id, video_id)
);

-- The stories a person has been given, used when users.subscribe_all is 0.
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    story   TEXT    NOT NULL,
    PRIMARY KEY (user_id, story)
);

-- Videos let through to a person who has a daily limit. `counted` is 0 for videos they chose to put
-- back on their list themselves ("Mark as not seen"), which never use up the day's new videos.
CREATE TABLE IF NOT EXISTS releases (
    user_id     INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    released_at REAL    NOT NULL,
    counted     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, video_id)
);

-- Where each person is in today's allowance: how much was still waiting when the day began
-- (`carried`) and how much has been released since (`released`), both in seconds.
CREATE TABLE IF NOT EXISTS release_days (
    user_id  INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    day      TEXT NOT NULL,
    carried  REAL NOT NULL,
    released REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            self._upgrade(conn)
        finally:
            conn.close()

    @staticmethod
    def _upgrade(conn: sqlite3.Connection) -> None:
        """Bring a database made by an older version up to date."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
        if "thumb" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN thumb TEXT")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "daily_minutes" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN daily_minutes INTEGER")
        if "subscribe_all" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN subscribe_all INTEGER NOT NULL DEFAULT 1")

    def connect(self) -> sqlite3.Connection:
        """Open a new connection. Connections are cheap; use one per request/thread."""
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, write: bool = True):
    """Run a group of statements all-or-nothing. `write` takes the write lock straight away, so
    two requests can't both act on the same stale reading."""
    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
