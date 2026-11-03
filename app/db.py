"""SQLite storage: users, sign-in sessions, the scanned video list, and watch progress."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL
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

    def connect(self) -> sqlite3.Connection:
        """Open a new connection. Connections are cheap; use one per request/thread."""
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn
