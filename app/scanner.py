"""Finds videos in the mounted folder and caches their metadata in the database.

The folder layout is simple: every top-level folder is one story (playlist) and the
videos inside it (including any sub-folders) are its episodes. The title and
description come from the metadata embedded in each video file; files without a
title fall back to their file name.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, Config
from .db import Database
from .naming import title_from_filename

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 60
MAX_DESCRIPTION = 4000
# Description-like tags, best first. Keys are lower-cased before lookup (mkv/webm use upper case).
DESCRIPTION_TAGS = ("description", "synopsis", "summary", "comment")
TRACK_TAGS = ("track", "episode_sort", "episode_id")


@dataclass
class Probe:
    title: str
    description: str
    track: int | None
    duration: float | None


@dataclass
class ScanStatus:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    found: int = 0
    added: int = 0
    updated: int = 0
    moved: int = 0
    waiting: int = 0  # still being copied in, will be picked up on a later scan
    unreadable: list[str] = field(default_factory=list)


def _first_int(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else None


def probe_video(cfg: Config, path: Path) -> Probe | None:
    """Read title/description/track/duration from the file. None if it isn't a readable video."""
    cmd = [cfg.ffprobe, "-v", "error", "-print_format", "json", "-show_format", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not run ffprobe on %s: %s", path, exc)
        return None
    if result.returncode != 0:
        log.warning("ffprobe could not read %s: %s", path, result.stderr.decode(errors="replace").strip())
        return None
    try:
        fmt = json.loads(result.stdout).get("format") or {}
    except ValueError:
        return None

    tags = {str(k).lower(): v.strip() for k, v in (fmt.get("tags") or {}).items() if isinstance(v, str)}
    description = next((tags[k] for k in DESCRIPTION_TAGS if tags.get(k)), "")
    track = next((n for k in TRACK_TAGS if (n := _first_int(tags.get(k, ""))) is not None), None)
    try:
        duration = float(fmt["duration"])
        if not duration > 0:
            duration = None
    except (KeyError, TypeError, ValueError):
        duration = None

    return Probe(
        title=tags.get("title") or title_from_filename(path.stem),
        description=description[:MAX_DESCRIPTION],
        track=track,
        duration=duration,
    )


def _ignored(name: str) -> bool:
    # Hidden files plus NAS housekeeping folders (@eaDir, #recycle, ...).
    return name.startswith((".", "@", "#")) or name == "lost+found"


def walk_library(root: Path) -> tuple[list[tuple[str, int, int]], dict[str, str]]:
    """Find everything of interest under root.

    Returns the videos as (relative '/'-separated path, size, mtime_ns), and the pictures as
    {video path without its extension: picture path}, e.g. 'Farm/Barn Cat' -> 'Farm/Barn Cat.png'.
    """
    videos: list[tuple[str, int, int]] = []
    pictures: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _ignored(d))
        for name in sorted(filenames):
            stem, ext = os.path.splitext(name)
            ext = ext.lower()
            is_video = ext in VIDEO_EXTENSIONS
            if _ignored(name) or not (is_video or ext in IMAGE_EXTENSIONS):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_video:
                videos.append((rel, st.st_size, st.st_mtime_ns))
                continue
            key = os.path.splitext(rel)[0]
            current = pictures.get(key)
            if current is None or IMAGE_EXTENSIONS.index(ext) < IMAGE_EXTENSIONS.index(os.path.splitext(current)[1].lower()):
                pictures[key] = rel
    return videos, pictures


def story_of(rel_path: str) -> str:
    return rel_path.split("/", 1)[0] if "/" in rel_path else ""


class Scanner:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.status = ScanStatus()
        self._scan_lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        # Files ffprobe rejected, keyed by path -> (size, mtime). Not retried until they change.
        self._rejected: dict[str, tuple[int, int]] = {}

    # -- background loop ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="video-scanner", daemon=True)
        self._thread.start()

    def request_scan(self) -> None:
        """Ask the background thread to scan right now."""
        self._wake.set()

    def _loop(self) -> None:
        while True:
            try:
                self.scan_once()
            except Exception:
                log.exception("Video scan failed")
            self._wake.wait(self.cfg.scan_interval)
            self._wake.clear()

    # -- one scan -------------------------------------------------------------------------

    def scan_once(self) -> ScanStatus:
        if not self._scan_lock.acquire(blocking=False):
            return self.status  # a scan is already running
        try:
            status = ScanStatus(running=True, started_at=time.time())
            self.status = status
            try:
                self._scan(status)
            except Exception as exc:
                status.error = f"Scan failed: {exc}"
                raise
            finally:
                status.running = False
                status.finished_at = time.time()
            return status
        finally:
            self._scan_lock.release()

    def _scan(self, status: ScanStatus) -> None:
        root = self.cfg.video_dir
        if not root.is_dir():
            status.error = f"The video folder {root} was not found."
            log.error(status.error)
            return

        files, pictures = walk_library(root)
        status.found = len(files)
        found_paths = {rel for rel, _, _ in files}
        now = time.time()

        conn = self.db.connect()
        try:
            existing = {r["rel_path"]: r for r in conn.execute("SELECT id, rel_path, size, mtime_ns FROM videos")}
            # Rows whose file has vanished, indexed by fingerprint, to spot moved/renamed files.
            vanished: dict[tuple[int, int], list[int]] = {}
            for row in existing.values():
                if row["rel_path"] not in found_paths:
                    vanished.setdefault((row["size"], row["mtime_ns"]), []).append(row["id"])

            for rel, size, mtime_ns in files:
                row = existing.get(rel)
                if row and row["size"] == size and row["mtime_ns"] == mtime_ns:
                    continue  # unchanged
                if now - mtime_ns / 1e9 < self.cfg.settle_seconds:
                    status.waiting += 1  # probably still being copied; try again next scan
                    continue
                if self._rejected.get(rel) == (size, mtime_ns):
                    continue

                # A file that moved keeps its identity (and everyone's watched marks).
                moved_id = None
                if row is None:
                    candidates = vanished.get((size, mtime_ns), [])
                    if len(candidates) == 1:
                        moved_id = candidates.pop()

                meta = probe_video(self.cfg, root / rel)
                if meta is None:
                    self._rejected[rel] = (size, mtime_ns)
                    status.unreadable.append(rel)
                    continue
                self._rejected.pop(rel, None)

                if moved_id is not None:
                    self._save_moved(conn, moved_id, rel, size, mtime_ns, meta)
                    status.moved += 1
                elif row is None:
                    self._save_new(conn, rel, size, mtime_ns, meta, now)
                    status.added += 1
                else:
                    self._save_changed(conn, row["id"], size, mtime_ns, meta)
                    status.updated += 1

            # Flag (never delete) videos whose file is gone, and unflag any that came back. Also
            # pair each video with the picture saved next to it. Pictures are checked on every scan,
            # so adding or swapping one never needs the video itself to be re-read.
            conn.execute("BEGIN")
            for row in conn.execute("SELECT id, rel_path, missing, thumb FROM videos").fetchall():
                gone = row["rel_path"] not in found_paths
                picture = pictures.get(os.path.splitext(row["rel_path"])[0])
                if gone != bool(row["missing"]) or picture != row["thumb"]:
                    conn.execute("UPDATE videos SET missing = ?, thumb = ? WHERE id = ?", (int(gone), picture, row["id"]))
            conn.execute("COMMIT")
        finally:
            conn.close()

        log.info(
            "Scan done: %d files, %d new, %d changed, %d moved, %d still copying, %d unreadable",
            status.found, status.added, status.updated, status.moved, status.waiting, len(status.unreadable),
        )

    @staticmethod
    def _save_new(conn, rel, size, mtime_ns, meta: Probe, now: float) -> None:
        conn.execute(
            """INSERT INTO videos (story, rel_path, title, description, track, duration, size, mtime_ns, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                   title = excluded.title, description = excluded.description, track = excluded.track,
                   duration = excluded.duration, size = excluded.size, mtime_ns = excluded.mtime_ns, missing = 0""",
            (story_of(rel), rel, meta.title, meta.description, meta.track, meta.duration, size, mtime_ns, now),
        )

    @staticmethod
    def _save_changed(conn, video_id, size, mtime_ns, meta: Probe) -> None:
        conn.execute(
            """UPDATE videos SET title = ?, description = ?, track = ?, duration = ?, size = ?, mtime_ns = ?
               WHERE id = ?""",
            (meta.title, meta.description, meta.track, meta.duration, size, mtime_ns, video_id),
        )

    @staticmethod
    def _save_moved(conn, video_id, rel, size, mtime_ns, meta: Probe) -> None:
        conn.execute(
            """UPDATE videos SET story = ?, rel_path = ?, title = ?, description = ?, track = ?,
                      duration = ?, size = ?, mtime_ns = ?, missing = 0
               WHERE id = ?""",
            (story_of(rel), rel, meta.title, meta.description, meta.track, meta.duration, size, mtime_ns, video_id),
        )
