"""What each person sees: stories, their unseen/seen videos, and watch progress."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .db import transaction
from .naming import natural_key, story_title

# A video counts as watched once it ends or is 90% played (so credits don't matter).
WATCHED_FRACTION = 0.9
# Don't bother resuming if the person only got a few seconds in.
MIN_RESUME_SECONDS = 10


def order_key(track: int | None, rel_path: str):
    """Playing order within a story: episode number from the file's metadata when present,
    otherwise file name order."""
    return (track is None, track or 0, natural_key(rel_path))


@dataclass
class Video:
    id: int
    story: str
    rel_path: str
    title: str
    description: str
    track: int | None
    duration: float | None
    position: float
    watched: bool
    thumb: str | None = None  # picture saved next to the video, if there is one

    @property
    def sort_key(self):
        return order_key(self.track, self.rel_path)

    @property
    def resume_at(self) -> float:
        if self.watched or self.position < MIN_RESUME_SECONDS:
            return 0
        if self.duration and self.position > self.duration - MIN_RESUME_SECONDS:
            return 0
        return self.position

    @property
    def percent_done(self) -> int:
        if not self.duration or not self.resume_at:
            return 0
        return max(1, min(99, round(100 * self.resume_at / self.duration)))


@dataclass
class Story:
    folder: str
    videos: list[Video] = field(default_factory=list)  # in playing order
    held_back: int = 0  # unseen videos not released yet (only for people with a daily limit)

    @property
    def title(self) -> str:
        return story_title(self.folder)

    @property
    def unseen(self) -> list[Video]:
        return [v for v in self.videos if not v.watched]

    @property
    def seen(self) -> list[Video]:
        return [v for v in self.videos if v.watched]

    @property
    def all_seen(self) -> bool:
        return bool(self.videos) and all(v.watched for v in self.videos) and not self.held_back

    @property
    def caught_up(self) -> bool:
        """Everything released so far is seen, but more will be released later."""
        return bool(self.videos) and all(v.watched for v in self.videos) and self.held_back > 0


# Which videos a person may see: those in a story they are subscribed to and, if they have a daily
# limit, the ones released to them (or that they have already started or finished).
SUBSCRIBED = "(u.subscribe_all = 1 OR v.story IN (SELECT story FROM subscriptions WHERE user_id = u.id))"
_FROM = """
    FROM videos v
    JOIN users u ON u.id = :user_id
    LEFT JOIN progress p ON p.video_id = v.id AND p.user_id = u.id
    LEFT JOIN releases r ON r.video_id = v.id AND r.user_id = u.id
"""
_VISIBLE = f"""
    v.missing = 0 AND {SUBSCRIBED}
    AND (u.daily_minutes IS NULL OR r.video_id IS NOT NULL OR p.video_id IS NOT NULL)
"""
_SELECT = (
    """SELECT v.id, v.story, v.rel_path, v.title, v.description, v.track, v.duration, v.thumb,
              COALESCE(p.position, 0) AS position, COALESCE(p.watched, 0) AS watched"""
    + _FROM + " WHERE " + _VISIBLE
)


def _video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"], story=row["story"], rel_path=row["rel_path"], title=row["title"],
        description=row["description"], track=row["track"], duration=row["duration"],
        position=row["position"], watched=bool(row["watched"]), thumb=row["thumb"],
    )


def held_back(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    """How many videos are being held back for tomorrow or later, per story. Empty for anyone
    without a daily limit."""
    rows = conn.execute(
        "SELECT v.story, COUNT(*) AS n" + _FROM
        + " WHERE v.missing = 0 AND " + SUBSCRIBED
        + " AND u.daily_minutes IS NOT NULL AND r.video_id IS NULL AND p.video_id IS NULL GROUP BY v.story",
        {"user_id": user_id},
    )
    return {row["story"]: row["n"] for row in rows}


def load_stories(conn: sqlite3.Connection, user_id: int, waiting: dict[str, int] | None = None) -> list[Story]:
    stories: dict[str, Story] = {}
    for row in conn.execute(_SELECT, {"user_id": user_id}):
        video = _video(row)
        stories.setdefault(video.story, Story(video.story)).videos.append(video)
    if waiting is None:
        waiting = held_back(conn, user_id)
    for story in stories.values():
        story.videos.sort(key=lambda v: v.sort_key)
        story.held_back = waiting.get(story.folder, 0)
    # Loose files in the top folder ('') go last; otherwise folder-name order.
    return sorted(stories.values(), key=lambda s: (s.folder == "", natural_key(s.folder)))


def get_video(conn: sqlite3.Connection, user_id: int, video_id: int) -> Video | None:
    row = conn.execute(_SELECT + " AND v.id = :video_id", {"user_id": user_id, "video_id": video_id}).fetchone()
    return _video(row) if row else None


def file_paths(conn: sqlite3.Connection, user_id: int, video_id: int) -> sqlite3.Row | None:
    """The video file and picture paths, if this person is allowed to see the video."""
    return conn.execute(
        "SELECT v.rel_path, v.thumb" + _FROM + " WHERE " + _VISIBLE + " AND v.id = :video_id",
        {"user_id": user_id, "video_id": video_id},
    ).fetchone()


def story_videos(conn: sqlite3.Connection, user_id: int, folder: str) -> list[Video]:
    rows = conn.execute(_SELECT + " AND v.story = :story", {"user_id": user_id, "story": folder})
    return sorted((_video(r) for r in rows), key=lambda v: v.sort_key)


def pick_next(videos: list[Video], current_id: int) -> Video | None:
    """The next unseen video after the current one, wrapping round to any earlier unseen one."""
    ids = [v.id for v in videos]
    if current_id not in ids:
        return None
    here = ids.index(current_id)
    others = videos[here + 1:] + videos[:here]
    return next((v for v in others if not v.watched), None)


def is_finished(position: float, duration: float, ended: bool) -> bool:
    return ended or (duration > 0 and position >= duration * WATCHED_FRACTION)


def record_progress(conn: sqlite3.Connection, user_id: int, video_id: int,
                    position: float, duration: float, ended: bool) -> bool | None:
    """Save where someone is in a video. Returns whether it's now watched, or None if no such video."""
    video = conn.execute(
        "SELECT v.duration" + _FROM + " WHERE " + _VISIBLE + " AND v.id = :video_id",
        {"user_id": user_id, "video_id": video_id},
    ).fetchone()
    if video is None:
        return None
    if duration <= 0:
        duration = video["duration"] or 0
    finished = is_finished(position, duration, ended)
    now = time.time()
    # Once watched, always watched: replaying a video never un-sees it.
    conn.execute(
        """INSERT INTO progress (user_id, video_id, position, watched, watched_at, updated_at)
           VALUES (:u, :v, :pos, :w, :wat, :now)
           ON CONFLICT(user_id, video_id) DO UPDATE SET
               position   = CASE WHEN progress.watched = 1 OR excluded.watched = 1 THEN 0 ELSE excluded.position END,
               watched    = MAX(progress.watched, excluded.watched),
               watched_at = CASE WHEN progress.watched = 0 AND excluded.watched = 1 THEN excluded.watched_at
                                 ELSE progress.watched_at END,
               updated_at = excluded.updated_at""",
        {"u": user_id, "v": video_id, "pos": 0 if finished else max(0.0, position),
         "w": int(finished), "wat": now if finished else None, "now": now},
    )
    row = conn.execute("SELECT watched FROM progress WHERE user_id = ? AND video_id = ?", (user_id, video_id)).fetchone()
    return bool(row["watched"])


def mark_unseen(conn: sqlite3.Connection, user_id: int, video_id: int) -> None:
    with transaction(conn):
        row = conn.execute("SELECT watched FROM progress WHERE user_id = ? AND video_id = ?", (user_id, video_id)).fetchone()
        if row is None:
            return
        conn.execute("DELETE FROM progress WHERE user_id = ? AND video_id = ?", (user_id, video_id))
        if row["watched"]:
            # Wanting to see an old favourite again is their own choice: keep it on their list
            # without it using up (or later counting against) the day's new videos.
            conn.execute(
                """INSERT INTO releases (user_id, video_id, released_at, counted) VALUES (?, ?, 0, 0)
                   ON CONFLICT(user_id, video_id) DO UPDATE SET counted = 0""",
                (user_id, video_id),
            )


# -- which stories a person is subscribed to ------------------------------------------------


def story_choices(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Every story in the library with its number of videos, in the order stories are listed."""
    rows = conn.execute("SELECT story, COUNT(*) AS n FROM videos WHERE missing = 0 GROUP BY story").fetchall()
    return sorted(((r["story"], r["n"]) for r in rows), key=lambda c: (c[0] == "", natural_key(c[0])))


def subscribed_stories(conn: sqlite3.Connection, user_id: int) -> set[str]:
    return {r["story"] for r in conn.execute("SELECT story FROM subscriptions WHERE user_id = ?", (user_id,))}


def set_subscriptions(conn: sqlite3.Connection, user_id: int, stories: set[str] | None) -> None:
    """Choose the stories a person sees. None means all of them, including ones added later."""
    with transaction(conn):
        if stories is None:
            conn.execute("UPDATE users SET subscribe_all = 1 WHERE id = ?", (user_id,))
            return
        known = {r["story"] for r in conn.execute("SELECT DISTINCT story FROM videos")}
        listed = {story for story, _ in story_choices(conn)}
        # A story whose files are all missing right now isn't shown to be ticked, so keep whatever
        # was chosen for it instead of forgetting it.
        keep = {s for s in subscribed_stories(conn, user_id) if s not in listed}
        conn.execute("UPDATE users SET subscribe_all = 0 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO subscriptions (user_id, story) VALUES (?, ?)",
            [(user_id, s) for s in sorted((stories & known) | keep)],
        )
