"""What each person sees: stories, their unseen/seen videos, and watch progress."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .naming import natural_key, story_title

# A video counts as watched once it ends or is 90% played (so credits don't matter).
WATCHED_FRACTION = 0.9
# Don't bother resuming if the person only got a few seconds in.
MIN_RESUME_SECONDS = 10


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
        # Episode number from the file's metadata when present, otherwise file name order.
        return (self.track is None, self.track or 0, natural_key(self.rel_path))

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
        return bool(self.videos) and all(v.watched for v in self.videos)


_SELECT = """
    SELECT v.id, v.story, v.rel_path, v.title, v.description, v.track, v.duration, v.thumb,
           COALESCE(p.position, 0) AS position, COALESCE(p.watched, 0) AS watched
    FROM videos v
    LEFT JOIN progress p ON p.video_id = v.id AND p.user_id = :user_id
    WHERE v.missing = 0
"""


def _video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"], story=row["story"], rel_path=row["rel_path"], title=row["title"],
        description=row["description"], track=row["track"], duration=row["duration"],
        position=row["position"], watched=bool(row["watched"]), thumb=row["thumb"],
    )


def load_stories(conn: sqlite3.Connection, user_id: int) -> list[Story]:
    stories: dict[str, Story] = {}
    for row in conn.execute(_SELECT, {"user_id": user_id}):
        video = _video(row)
        stories.setdefault(video.story, Story(video.story)).videos.append(video)
    for story in stories.values():
        story.videos.sort(key=lambda v: v.sort_key)
    # Loose files in the top folder ('') go last; otherwise folder-name order.
    return sorted(stories.values(), key=lambda s: (s.folder == "", natural_key(s.folder)))


def get_video(conn: sqlite3.Connection, user_id: int, video_id: int) -> Video | None:
    row = conn.execute(_SELECT + " AND v.id = :video_id", {"user_id": user_id, "video_id": video_id}).fetchone()
    return _video(row) if row else None


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
    video = conn.execute("SELECT duration FROM videos WHERE id = ? AND missing = 0", (video_id,)).fetchone()
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
    conn.execute("DELETE FROM progress WHERE user_id = ? AND video_id = ?", (user_id, video_id))
