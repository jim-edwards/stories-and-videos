"""Daily release: lets a person see only so much new video each day.

A person with a daily amount (say 30 minutes) doesn't see every unwatched video at once. Videos are
"released" to them, in their playing order, and only released ones appear. Each day (the day turns
over at a time the administrator picks, 1:00 AM by default) the list is topped up so that what is
waiting to be watched never adds up to more than the daily amount:

* Whatever they didn't get round to yesterday is still there, and counts towards today's amount.
* Stories take turns, one video each, so no single story hogs the day. The story that went longest
  without a release goes first. A story's next video never jumps the queue: if it is too long to fit
  today, that story simply waits.
* Watching things doesn't free up more: the day's amount is fixed when the day begins.
* A video longer than the whole daily amount would never fit, so it is released on its own, when
  nothing else is waiting.
* Videos the person has already seen can always be watched again, and never count against the day.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone, tzinfo

from .auth import UserError
from .db import transaction
from .library import SUBSCRIBED, held_back, order_key
from .naming import natural_key

DEFAULT_RELEASE_TIME = "01:00"
MAX_DAILY_MINUTES = 600
# Used for the rare video whose length couldn't be read.
UNKNOWN_SECONDS = 10 * 60

_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# -- when the day turns over ----------------------------------------------------------------


def parse_release_time(text: str) -> clock_time:
    match = _CLOCK.match((text or "").strip())
    if not match:
        raise UserError("Please give the time as hours and minutes, for example 01:00 for 1 AM.")
    return clock_time(int(match.group(1)), int(match.group(2)))


def get_release_time(conn: sqlite3.Connection) -> clock_time:
    row = conn.execute("SELECT value FROM settings WHERE key = 'release_time'").fetchone()
    try:
        return parse_release_time(row["value"] if row else DEFAULT_RELEASE_TIME)
    except UserError:
        return parse_release_time(DEFAULT_RELEASE_TIME)


def set_release_time(conn: sqlite3.Connection, text: str) -> clock_time:
    value = parse_release_time(text)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('release_time', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (value.strftime("%H:%M"),),
    )
    return value


def _utc(moment: datetime) -> datetime:
    # Compared as exact instants: two times in the same zone are otherwise compared as clock readings,
    # which goes wrong for the repeated hour when the clocks go back.
    return moment.astimezone(timezone.utc)


def day_start(now: datetime, reset: clock_time) -> datetime:
    """The most recent moment, at or before `now`, when the day turned over."""
    zone = now.tzinfo
    start = datetime.combine(now.date(), reset, tzinfo=zone)
    if _utc(start) > _utc(now):
        start = datetime.combine(now.date() - timedelta(days=1), reset, tzinfo=zone)
    return start


def day_label(now: datetime, reset: clock_time) -> str:
    """Names the current day, e.g. '2026-11-04'. It changes when the day turns over, not at midnight."""
    return day_start(now, reset).date().isoformat()


def next_reset(now: datetime, reset: clock_time) -> datetime:
    return datetime.combine(day_start(now, reset).date() + timedelta(days=1), reset, tzinfo=now.tzinfo)


def _local(zone: tzinfo, now: float | None) -> datetime:
    return datetime.fromtimestamp(time.time() if now is None else now, zone)


# -- a person's daily amount ----------------------------------------------------------------


def set_daily_minutes(conn: sqlite3.Connection, user_id: int, minutes: int | None) -> None:
    """Give someone a daily amount, or None to let them see every video at once."""
    if minutes is not None and not 1 <= minutes <= MAX_DAILY_MINUTES:
        raise UserError(f"Please choose between 1 and {MAX_DAILY_MINUTES} minutes a day.")
    conn.execute("UPDATE users SET daily_minutes = ? WHERE id = ?", (minutes, user_id))


def _remaining(duration: float | None, position: float) -> float:
    return max(0.0, (duration or UNKNOWN_SECONDS) - position)


def waiting_seconds(conn: sqlite3.Connection, user_id: int) -> float:
    """Released videos that haven't been watched yet, as time still to go (a half-watched video
    counts for what is left of it). Videos the person put back on their list themselves don't count."""
    rows = conn.execute(
        """SELECT v.duration, COALESCE(p.position, 0) AS position
           FROM releases r
           JOIN users u  ON u.id = r.user_id
           JOIN videos v ON v.id = r.video_id AND v.missing = 0
           LEFT JOIN progress p ON p.user_id = r.user_id AND p.video_id = r.video_id
           WHERE r.user_id = ? AND r.counted = 1 AND COALESCE(p.watched, 0) = 0 AND """ + SUBSCRIBED,
        (user_id,),
    )
    return sum(_remaining(row["duration"], row["position"]) for row in rows)


def _candidates(conn: sqlite3.Connection, user_id: int) -> dict[str, list[sqlite3.Row]]:
    """Each subscribed story's videos that haven't been released, seen or started, in playing order."""
    rows = conn.execute(
        """SELECT v.id, v.story, v.rel_path, v.track, v.duration
           FROM videos v
           JOIN users u ON u.id = ?
           LEFT JOIN releases r ON r.user_id = u.id AND r.video_id = v.id
           LEFT JOIN progress p ON p.user_id = u.id AND p.video_id = v.id
           WHERE v.missing = 0 AND r.video_id IS NULL AND p.video_id IS NULL AND """ + SUBSCRIBED,
        (user_id,),
    ).fetchall()
    stories: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        stories.setdefault(row["story"], []).append(row)
    for videos in stories.values():
        videos.sort(key=lambda r: order_key(r["track"], r["rel_path"]))
    return stories


def _turn_order(conn: sqlite3.Connection, user_id: int, stories) -> list[str]:
    """Stories in the order they take turns: the one that went longest without a release first."""
    last = {
        row["story"]: row["last"]
        for row in conn.execute(
            "SELECT v.story, MAX(r.released_at) AS last FROM releases r JOIN videos v ON v.id = r.video_id "
            "WHERE r.user_id = ? GROUP BY v.story",
            (user_id,),
        )
    }
    return sorted(stories, key=lambda s: (last.get(s, -1.0), s == "", natural_key(s)))


def choose_videos(stories: dict[str, list[sqlite3.Row]], order: list[str], room: float,
                  limit: float, day_is_empty: bool) -> list[sqlite3.Row]:
    """Pick what to release: one video from each story in turn, then a second from each, and so on,
    as long as they fit in `room` seconds. `limit` is the whole day's amount and `day_is_empty`
    says nothing else is waiting or released yet today (so one over-long video may go out alone)."""
    queues = {story: list(stories[story]) for story in order}
    chosen: list[sqlite3.Row] = []
    blocked: set[str] = set()
    progressed = True
    while progressed:
        progressed = False
        for story in order:
            queue = queues[story]
            if story in blocked or not queue:
                continue
            video = queue[0]
            length = video["duration"] or UNKNOWN_SECONDS
            if length <= room:
                chosen.append(queue.pop(0))
                room -= length
                progressed = True
            elif day_is_empty and not chosen and length > limit:
                return [queue[0]]  # longer than a whole day's amount: it has to go out on its own
            else:
                blocked.add(story)  # too long for now; the story waits rather than skipping ahead
    return chosen


def ensure_released(conn: sqlite3.Connection, user_id: int, zone: tzinfo, now: float | None = None) -> int:
    """Top up what someone with a daily amount can see. Safe to call as often as you like: it does
    nothing unless the day has turned over or there is still room left in today's amount. Returns
    how many videos were newly released."""
    user = conn.execute("SELECT daily_minutes FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or user["daily_minutes"] is None:
        return 0
    limit = user["daily_minutes"] * 60.0
    moment = _local(zone, now)
    stamp = moment.timestamp()
    day = day_label(moment, get_release_time(conn))

    state = conn.execute("SELECT day, carried, released FROM release_days WHERE user_id = ?", (user_id,)).fetchone()
    if state and state["day"] >= day and state["carried"] + state["released"] >= limit:
        return 0  # today's amount is already used up; no need to take the write lock

    with transaction(conn):
        state = conn.execute("SELECT day, carried, released FROM release_days WHERE user_id = ?", (user_id,)).fetchone()
        # Days only move forward: moving the turnover time later in the day must not hand out a second helping.
        if state is None or state["day"] < day:
            carried, released = waiting_seconds(conn, user_id), 0.0
            conn.execute(
                """INSERT INTO release_days (user_id, day, carried, released) VALUES (?, ?, ?, 0)
                   ON CONFLICT(user_id) DO UPDATE SET day = excluded.day, carried = excluded.carried, released = 0""",
                (user_id, day, carried),
            )
        else:
            carried, released = state["carried"], state["released"]

        room = limit - carried - released
        if room <= 0:
            return 0
        stories = _candidates(conn, user_id)
        if not stories:
            return 0
        chosen = choose_videos(stories, _turn_order(conn, user_id, stories), room, limit,
                               day_is_empty=carried + released == 0)
        # Each video is stamped a moment after the last, so the story that was just served is
        # the most recent one and takes its turn later next time.
        conn.executemany(
            "INSERT INTO releases (user_id, video_id, released_at, counted) VALUES (?, ?, ?, 1)",
            [(user_id, video["id"], stamp + i * 0.001) for i, video in enumerate(chosen)],
        )
        conn.execute(
            "UPDATE release_days SET released = released + ? WHERE user_id = ?",
            (sum(video["duration"] or UNKNOWN_SECONDS for video in chosen), user_id),
        )
        return len(chosen)


# -- for the Manage page --------------------------------------------------------------------


@dataclass
class Summary:
    minutes: int | None       # the daily amount, None when there is no limit
    waiting: float            # seconds released and still unwatched
    held_back: int            # videos still being held back for later days
    next_reset: datetime


def summarize(conn: sqlite3.Connection, user_id: int, zone: tzinfo, now: float | None = None) -> Summary:
    user = conn.execute("SELECT daily_minutes FROM users WHERE id = ?", (user_id,)).fetchone()
    return Summary(
        minutes=user["daily_minutes"],
        waiting=waiting_seconds(conn, user_id),
        held_back=sum(held_back(conn, user_id).values()),
        next_reset=next_reset(_local(zone, now), get_release_time(conn)),
    )
