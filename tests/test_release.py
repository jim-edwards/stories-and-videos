"""The daily release: how much new video a person can see each day, and in what order."""

from datetime import datetime, timedelta, timezone

import pytest

from app import library, release
from app.auth import UserError

CHICAGO_LIKE = timezone(timedelta(hours=-6))  # a fixed offset: these tests are about the rules, not about clocks


def at(day, hour=12, minute=0):
    """A moment in November 2026, as seconds since 1970."""
    return datetime(2026, 11, day, hour, minute, tzinfo=CHICAGO_LIKE).timestamp()


def make_library(db, **stories):
    """Replace the scanned sample library with stories of the given video lengths, in minutes.

    make_library(db, A=[10, 10], B=[30]) makes A1, A2 (10 minutes each) and B1 (30 minutes)."""
    db.execute("DELETE FROM videos")
    ids = {}
    for story, lengths in stories.items():
        for n, minutes in enumerate(lengths, 1):
            cur = db.execute(
                "INSERT INTO videos (story, rel_path, title, duration, size, mtime_ns, added_at) VALUES (?, ?, ?, ?, 1, 1, 1)",
                (story, f"{story}/{n:02d}.mp4", f"{story}{n}", None if minutes is None else minutes * 60),
            )
            ids[f"{story}{n}"] = cur.lastrowid
    return ids


def limit(db, user, minutes):
    release.set_daily_minutes(db, user, minutes)


def top_up(db, user, day, hour=12, minute=0):
    return release.ensure_released(db, user, CHICAGO_LIKE, now=at(day, hour, minute))


def waiting(db, user):
    """Titles of the unseen videos the person can see, story by story in playing order."""
    return [v.title for s in library.load_stories(db, user) for v in s.unseen]


def watch(db, user, *video_ids, fraction=None):
    for vid in video_ids:
        if fraction is None:
            library.record_progress(db, user, vid, 1, 1, ended=True)
        else:
            duration = db.execute("SELECT duration FROM videos WHERE id = ?", (vid,)).fetchone()["duration"]
            library.record_progress(db, user, vid, duration * fraction, duration, ended=False)


# -- everyone sees everything until they are given a daily amount ---------------------------


def test_without_a_daily_amount_everything_is_visible_and_nothing_is_released(db, mom):
    make_library(db, A=[10, 10], B=[10])
    assert waiting(db, mom) == ["A1", "A2", "B1"]
    assert top_up(db, mom, 4) == 0
    assert db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0


def test_a_daily_amount_hides_everything_until_it_is_released(db, mom):
    make_library(db, A=[10, 10])
    limit(db, mom, 30)
    assert waiting(db, mom) == []


# -- how much, and from which stories -------------------------------------------------------


def test_stories_take_turns_and_the_day_never_goes_over(db, mom):
    make_library(db, A=[10, 10, 10], B=[10, 10, 10])
    limit(db, mom, 30)
    assert top_up(db, mom, 4) == 3
    # One from each story first, then a second from the story whose turn comes round again.
    assert waiting(db, mom) == ["A1", "A2", "B1"]
    assert top_up(db, mom, 4, hour=18) == 0


def test_the_story_that_waited_longest_goes_first_the_next_day(db, mom):
    ids = make_library(db, A=[10, 10, 10], B=[10, 10, 10])
    limit(db, mom, 30)
    top_up(db, mom, 4)
    watch(db, mom, ids["A1"], ids["A2"], ids["B1"])
    assert top_up(db, mom, 5) == 3
    assert waiting(db, mom) == ["A3", "B2", "B3"]


def test_videos_are_released_in_playing_order_and_seen_ones_are_skipped(db, mom):
    ids = make_library(db, A=[10, 10, 10, 10])
    watch(db, mom, ids["A2"])  # seen before there was any limit
    limit(db, mom, 20)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["A1", "A3"]
    watch(db, mom, ids["A1"], ids["A3"])
    top_up(db, mom, 5)
    assert waiting(db, mom) == ["A4"]


def test_track_numbers_decide_the_order_like_everywhere_else(db, mom):
    ids = make_library(db, A=[10, 10, 10])
    db.execute("UPDATE videos SET track = 3 WHERE id = ?", (ids["A1"],))
    db.execute("UPDATE videos SET track = 1 WHERE id = ?", (ids["A3"],))
    db.execute("UPDATE videos SET track = 2 WHERE id = ?", (ids["A2"],))
    limit(db, mom, 20)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["A3", "A2"]  # shown in playing order: track 1 then 2


def test_a_story_never_skips_ahead_when_its_next_video_is_too_long_for_now(db, mom):
    make_library(db, A=[20, 20, 5], B=[15, 5])
    limit(db, mom, 30)
    top_up(db, mom, 4)
    # A1 fits (20). B1 (15) and A2 (20) don't fit in the 10 left, so their stories wait;
    # the short videos behind them must not jump the queue.
    assert waiting(db, mom) == ["A1"]


def test_unknown_lengths_count_as_ten_minutes(db, mom):
    make_library(db, A=[None, None, None, None, None])
    limit(db, mom, 30)
    assert top_up(db, mom, 4) == 3


def test_videos_that_are_missing_or_in_the_wrong_state_are_never_released(db, mom):
    ids = make_library(db, A=[10, 10, 10])
    db.execute("UPDATE videos SET missing = 1 WHERE id = ?", (ids["A1"],))
    limit(db, mom, 30)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["A2", "A3"]


# -- carrying on from one day to the next ---------------------------------------------------


def test_what_was_not_watched_still_counts_towards_the_next_day(db, mom):
    ids = make_library(db, A=[10] * 6)
    limit(db, mom, 30)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["A1", "A2", "A3"]
    watch(db, mom, ids["A1"])
    top_up(db, mom, 5)
    assert waiting(db, mom) == ["A2", "A3", "A4"]  # 20 minutes were left, so only 10 more
    top_up(db, mom, 6)
    assert waiting(db, mom) == ["A2", "A3", "A4"]  # nothing watched: no more than 30 minutes ever waits


def test_watching_does_not_release_more_the_same_day(db, mom):
    ids = make_library(db, A=[10] * 6)
    limit(db, mom, 30)
    top_up(db, mom, 4)
    watch(db, mom, ids["A1"], ids["A2"], ids["A3"])
    assert top_up(db, mom, 4, hour=20) == 0
    assert waiting(db, mom) == []


def test_a_half_watched_video_counts_only_for_what_is_left(db, mom):
    ids = make_library(db, A=[20, 20, 20])
    limit(db, mom, 30)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["A1"]
    watch(db, mom, ids["A1"], fraction=0.5)
    top_up(db, mom, 5)
    assert waiting(db, mom) == ["A1", "A2"]  # 10 minutes left + 20 new = 30


def test_the_day_turns_over_at_the_chosen_time_not_at_midnight(db, mom):
    ids = make_library(db, A=[10] * 6)
    limit(db, mom, 10)
    assert top_up(db, mom, 4, hour=2) == 1               # the 4th, after the default 1 AM
    watch(db, mom, ids["A1"])
    assert top_up(db, mom, 4, hour=23, minute=59) == 0
    assert top_up(db, mom, 5, hour=0, minute=30) == 0    # past midnight, but not yet 1 AM
    assert top_up(db, mom, 5, hour=1) == 1


def test_a_later_turnover_time_can_be_chosen(db, mom):
    ids = make_library(db, A=[10] * 6)
    release.set_release_time(db, "06:00")
    limit(db, mom, 10)
    assert top_up(db, mom, 4, hour=7) == 1
    watch(db, mom, ids["A1"])
    assert top_up(db, mom, 5, hour=5, minute=59) == 0
    assert top_up(db, mom, 5, hour=6) == 1


def test_moving_the_turnover_time_later_does_not_hand_out_a_second_helping(db, mom):
    ids = make_library(db, A=[10] * 6)
    limit(db, mom, 10)
    top_up(db, mom, 4, hour=3)
    watch(db, mom, ids["A1"])
    release.set_release_time(db, "06:00")  # 3:30 AM now counts as "before the day turns over"
    assert top_up(db, mom, 4, hour=3, minute=30) == 0


# -- changes during the day -----------------------------------------------------------------


def test_videos_added_later_in_the_day_fill_only_the_room_that_was_left(db, mom):
    make_library(db, A=[10])
    limit(db, mom, 30)
    top_up(db, mom, 4, hour=2)
    assert waiting(db, mom) == ["A1"]
    for n in (1, 2, 3):
        db.execute(
            "INSERT INTO videos (story, rel_path, title, duration, size, mtime_ns, added_at) VALUES ('B', ?, ?, 600, 1, 1, 1)",
            (f"B/{n}.mp4", f"B{n}"),
        )
    assert top_up(db, mom, 4, hour=15) == 2
    assert waiting(db, mom) == ["A1", "B1", "B2"]


def test_raising_the_amount_releases_more_today_and_lowering_it_takes_nothing_back(db, mom):
    make_library(db, A=[10] * 8)
    limit(db, mom, 30)
    top_up(db, mom, 4)
    limit(db, mom, 50)
    assert top_up(db, mom, 4, hour=13) == 2
    assert len(waiting(db, mom)) == 5
    limit(db, mom, 10)
    assert top_up(db, mom, 4, hour=14) == 0
    assert len(waiting(db, mom)) == 5


def test_switching_a_daily_amount_off_shows_everything_again(db, mom):
    make_library(db, A=[10] * 4)
    limit(db, mom, 10)
    top_up(db, mom, 4)
    assert len(waiting(db, mom)) == 1
    limit(db, mom, None)
    assert len(waiting(db, mom)) == 4


# -- videos longer than the whole day -------------------------------------------------------


def test_a_video_longer_than_the_daily_amount_is_released_on_its_own(db, mom):
    ids = make_library(db, A=[45, 5])
    limit(db, mom, 30)
    assert top_up(db, mom, 4) == 1
    assert waiting(db, mom) == ["A1"]
    assert top_up(db, mom, 5) == 0  # still unwatched: nothing else piles on
    watch(db, mom, ids["A1"])
    top_up(db, mom, 6)
    assert waiting(db, mom) == ["A2"]


def test_a_long_video_gets_its_turn_and_is_not_starved_by_short_ones(db, mom):
    ids = make_library(db, B=[10] * 10, Z=[45])
    limit(db, mom, 30)
    top_up(db, mom, 4)
    assert waiting(db, mom) == ["B1", "B2", "B3"]  # Z1 is too long to fit beside them
    watch(db, mom, ids["B1"], ids["B2"], ids["B3"])
    top_up(db, mom, 5)
    assert waiting(db, mom) == ["Z1"]               # Z's turn: it goes out alone
    watch(db, mom, ids["Z1"])
    top_up(db, mom, 6)
    assert waiting(db, mom) == ["B4", "B5", "B6"]


# -- putting a video back on the list -------------------------------------------------------


def test_marking_a_video_not_seen_keeps_it_on_the_list_without_using_up_new_ones(db, mom):
    ids = make_library(db, A=[10] * 6)
    limit(db, mom, 30)
    top_up(db, mom, 4)
    watch(db, mom, ids["A1"], ids["A2"], ids["A3"])
    library.mark_unseen(db, mom, ids["A1"])
    top_up(db, mom, 5)
    # A1 is back on the list but only A1 is "old"; the full 30 minutes of new videos arrive.
    assert waiting(db, mom) == ["A1", "A4", "A5", "A6"]


def test_marking_a_video_seen_before_the_limit_not_seen_does_not_hide_it(db, mom):
    ids = make_library(db, A=[10] * 3)
    watch(db, mom, ids["A1"])
    limit(db, mom, 10)
    library.mark_unseen(db, mom, ids["A1"])
    assert "A1" in waiting(db, mom)


def test_marking_an_unwatched_or_unreleased_video_not_seen_releases_nothing(db, mom):
    ids = make_library(db, A=[10] * 3)
    limit(db, mom, 10)
    library.mark_unseen(db, mom, ids["A3"])
    assert waiting(db, mom) == []
    assert db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0


def test_each_person_has_their_own_releases(db, mom):
    from app import auth
    dad = auth.create_user(db, "dad", "dads password")
    make_library(db, A=[10] * 4)
    limit(db, mom, 10)
    limit(db, dad, 30)
    top_up(db, mom, 4)
    top_up(db, dad, 4)
    assert len(waiting(db, mom)) == 1 and len(waiting(db, dad)) == 3


def test_removing_a_person_removes_their_releases_and_choices(db, mom):
    make_library(db, A=[10] * 2)
    limit(db, mom, 10)
    library.set_subscriptions(db, mom, {"A"})
    top_up(db, mom, 4)
    from app import auth
    auth.delete_user(db, mom)
    for table in ("releases", "release_days", "subscriptions"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# -- the clock ------------------------------------------------------------------------------


def test_release_time_must_be_a_real_time_of_day(db):
    assert release.set_release_time(db, "6:30").strftime("%H:%M") == "06:30"
    assert release.get_release_time(db).strftime("%H:%M") == "06:30"
    for bad in ("", "25:00", "1:75", "noon", "1 AM", "01:00:00"):
        with pytest.raises(UserError):
            release.set_release_time(db, bad)
    assert release.get_release_time(db).strftime("%H:%M") == "06:30"


def test_the_default_release_time_is_one_in_the_morning(db):
    assert release.get_release_time(db).strftime("%H:%M") == "01:00"


def test_daily_amount_must_be_sensible(db, mom):
    for bad in (0, -5, release.MAX_DAILY_MINUTES + 1):
        with pytest.raises(UserError):
            release.set_daily_minutes(db, mom, bad)


def test_day_labels_follow_the_turnover_time():
    reset = release.parse_release_time("01:00")
    day = lambda d, h, m=0: release.day_label(datetime(2026, 11, d, h, m, tzinfo=CHICAGO_LIKE), reset)
    assert day(4, 0, 59) == "2026-11-03" and day(4, 1) == "2026-11-04" and day(4, 23, 59) == "2026-11-04"
    nxt = release.next_reset(datetime(2026, 11, 4, 12, tzinfo=CHICAGO_LIKE), reset)
    assert (nxt.day, nxt.hour) == (5, 1)
    nxt = release.next_reset(datetime(2026, 11, 4, 0, 30, tzinfo=CHICAGO_LIKE), reset)
    assert (nxt.day, nxt.hour) == (4, 1)


def test_the_day_never_goes_backwards_when_the_clocks_change():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        chicago = zoneinfo.ZoneInfo("America/Chicago")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no time zone database on this machine")
    reset = release.parse_release_time("01:30")
    # On 1 November 2026 the clocks go back at 2:00 AM, so 1:00-2:00 happens twice.
    first = datetime(2026, 11, 1, 6, 45, tzinfo=timezone.utc).astimezone(chicago)   # 1:45 AM the first time
    again = datetime(2026, 11, 1, 7, 15, tzinfo=timezone.utc).astimezone(chicago)   # 1:15 AM the second time
    assert (first.hour, again.hour) == (1, 1)
    assert release.day_label(first, reset) == release.day_label(again, reset) == "2026-11-01"
