"""Choosing which stories a person sees, and the daily release as people actually meet it (the web pages)."""

import os
import re
import shutil
import time

from app import library, release
from conftest import csrf_of, video_id
from test_views import page, story_html

ALL_STORIES = ["Gamma Story", "Alpha Tales", "Beta", "Other Videos"]


def titles_on_home(db, user):
    return [s.title for s in library.load_stories(db, user)]


# -- subscriptions --------------------------------------------------------------------------


def test_everyone_is_subscribed_to_everything_to_begin_with(db, mom):
    assert titles_on_home(db, mom) == ALL_STORIES


def test_a_person_sees_only_the_stories_they_are_subscribed_to(db, mom):
    library.set_subscriptions(db, mom, {"Beta", "Alpha Tales"})
    assert titles_on_home(db, mom) == ["Alpha Tales", "Beta"]
    assert library.subscribed_stories(db, mom) == {"Alpha Tales", "Beta"}
    # Other people are not affected.
    from app import auth
    dad = auth.create_user(db, "dad", "dads password")
    assert titles_on_home(db, dad) == ALL_STORIES


def test_loose_videos_can_be_subscribed_to_too(db, mom):
    library.set_subscriptions(db, mom, {""})
    assert titles_on_home(db, mom) == ["Other Videos"]


def test_subscribing_to_nothing_shows_nothing(db, mom):
    library.set_subscriptions(db, mom, set())
    assert titles_on_home(db, mom) == []


def test_going_back_to_everything_keeps_the_earlier_choice_for_later_but_shows_all(db, mom):
    library.set_subscriptions(db, mom, {"Beta"})
    library.set_subscriptions(db, mom, None)
    assert titles_on_home(db, mom) == ALL_STORIES


def test_names_that_are_not_stories_are_ignored(db, mom):
    library.set_subscriptions(db, mom, {"Beta", "No Such Story", "../etc"})
    assert library.subscribed_stories(db, mom) == {"Beta"}


def test_a_story_whose_files_are_all_missing_right_now_keeps_its_subscription(db, mom):
    library.set_subscriptions(db, mom, {"Beta", "Alpha Tales"})
    db.execute("UPDATE videos SET missing = 1 WHERE story = 'Beta'")  # e.g. the NAS share is offline
    library.set_subscriptions(db, mom, {"Alpha Tales"})                # saved while Beta is not listed
    assert library.subscribed_stories(db, mom) == {"Alpha Tales", "Beta"}


def test_story_choices_list_each_story_with_its_video_count(db):
    assert library.story_choices(db) == [("05 - Gamma Story", 1), ("Alpha Tales", 3), ("Beta", 2), ("", 1)]


def test_new_stories_reach_people_subscribed_to_everything_but_not_those_with_a_choice(
        services, db, mom, library_dir, sample_videos):
    from app import auth
    dad = auth.create_user(db, "dad", "dads password")
    library.set_subscriptions(db, dad, {"Beta"})
    (library_dir / "Delta").mkdir()
    shutil.copy(sample_videos / "loose.mp4", library_dir / "Delta" / "one.mp4")
    old = time.time() - 3600
    os.utime(library_dir / "Delta" / "one.mp4", (old, old))
    services.scanner.scan_once()
    assert "Delta" in titles_on_home(db, mom)
    assert titles_on_home(db, dad) == ["Beta"]


def test_unsubscribed_videos_cannot_be_watched_streamed_or_recorded(mom_client, db, mom):
    library.set_subscriptions(db, mom, {"Beta"})
    one = video_id(db, "One")         # in Alpha Tales: not subscribed
    first = video_id(db, "First")     # in Beta
    headers = {"X-CSRF-Token": csrf_of(mom_client)}
    assert mom_client.get(f"/watch/{one}").status_code == 404
    assert mom_client.get(f"/media/{one}").status_code == 404
    assert mom_client.get(f"/thumb/{one}").status_code == 404
    assert mom_client.post(f"/api/progress/{one}", json={"position": 1, "duration": 2}, headers=headers).status_code == 404
    assert mom_client.get(f"/watch/{first}").status_code == 200
    assert mom_client.get(f"/media/{first}").status_code == 200
    html = page(mom_client)
    assert "Beta" in html and "Alpha Tales" not in html and "Gamma" not in html


def test_nothing_is_recorded_for_a_video_the_person_may_not_see(mom_client, db, mom):
    library.set_subscriptions(db, mom, {"Beta"})
    mom_client.post(f"/api/progress/{video_id(db, 'One')}", json={"position": 1, "duration": 2},
                    headers={"X-CSRF-Token": csrf_of(mom_client)})
    assert db.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 0


def test_next_video_only_offers_stories_the_person_can_see(db, mom):
    assert [v.title for v in library.story_videos(db, mom, "Alpha Tales")] == ["One", "Two", "Ten"]
    library.set_subscriptions(db, mom, {"Beta"})
    assert library.story_videos(db, mom, "Alpha Tales") == []


# -- the daily release on the pages people see ----------------------------------------------


def give_daily_amount(db, user, minutes, seconds_each=600):
    db.execute("UPDATE videos SET duration = ?", (seconds_each,))
    release.set_daily_minutes(db, user, minutes)


def test_the_home_page_releases_the_first_videos_when_a_person_visits(mom_client, db, mom):
    give_daily_amount(db, mom, 20)                       # 10-minute videos: two fit
    html = page(mom_client)
    assert len(re.findall(r'<article class="card', html)) == 2
    assert db.execute("SELECT COUNT(*) FROM releases WHERE user_id = ?", (mom,)).fetchone()[0] == 2


def test_videos_not_yet_released_cannot_be_watched_or_streamed(mom_client, db, mom):
    give_daily_amount(db, mom, 10)                       # one video: the first story's first video
    page(mom_client)
    released = {r["video_id"] for r in db.execute("SELECT video_id FROM releases WHERE user_id = ?", (mom,))}
    held = [r["id"] for r in db.execute("SELECT id FROM videos") if r["id"] not in released]
    assert len(released) == 1 and held
    headers = {"X-CSRF-Token": csrf_of(mom_client)}
    for vid in held:
        assert mom_client.get(f"/watch/{vid}").status_code == 404
        assert mom_client.get(f"/media/{vid}").status_code == 404
        assert mom_client.get(f"/thumb/{vid}").status_code == 404
        assert mom_client.post(f"/api/progress/{vid}", json={"position": 1, "duration": 2}, headers=headers).status_code == 404
        assert mom_client.post(f"/video/{vid}/unseen", data={"csrf_token": headers["X-CSRF-Token"]}).status_code == 302
    assert mom_client.get(f"/watch/{next(iter(released))}").status_code == 200
    assert db.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM releases WHERE user_id = ?", (mom,)).fetchone()[0] == 1  # nothing slipped in


def test_when_everything_released_is_watched_the_page_says_more_comes_tomorrow(mom_client, db, mom):
    give_daily_amount(db, mom, 20)                       # Gamma's only video and Alpha Tales' first
    page(mom_client)
    for row in db.execute("SELECT video_id FROM releases").fetchall():
        library.record_progress(db, mom, row["video_id"], 600, 600, ended=True)
    html = page(mom_client)
    assert "You have watched everything for today. More will be ready tomorrow." in html
    gamma, alpha = story_html(html, "Gamma Story"), story_html(html, "Alpha Tales")
    assert 'class="story done"' in gamma and "You have seen all of these" in gamma   # nothing more to come
    assert 'class="story done"' not in alpha                                         # more is on its way
    assert "You have seen today's videos from this story. More will be ready tomorrow." in alpha
    assert "Already seen (1)" in alpha and "to watch" not in alpha


def test_a_person_with_no_daily_amount_sees_no_such_messages(mom_client, db, mom):
    html = page(mom_client)
    assert "tomorrow" not in html


def test_when_the_days_videos_are_hidden_the_page_says_nothing_is_new_yet(mom_client, db, mom):
    give_daily_amount(db, mom, 10)
    page(mom_client)                                     # today's one video comes from the first story
    library.set_subscriptions(db, mom, {"Beta"})         # ...which is then taken off their list
    html = page(mom_client)                              # today's amount is used up, so Beta waits
    assert "nothing new to watch right now. More will be ready tomorrow" in html
    assert "<article" not in html


def test_a_story_in_the_seen_list_can_still_be_watched_again_over_the_limit(mom_client, db, mom):
    give_daily_amount(db, mom, 10)
    page(mom_client)
    vid = db.execute("SELECT video_id FROM releases").fetchone()["video_id"]
    library.record_progress(db, mom, vid, 600, 600, ended=True)
    page(mom_client)
    assert mom_client.get(f"/watch/{vid}").status_code == 200
    assert mom_client.get(f"/media/{vid}").status_code == 200
    assert db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1  # watching again released nothing


def test_marking_unseen_on_the_page_keeps_the_video_listed(mom_client, db, mom):
    give_daily_amount(db, mom, 10)
    page(mom_client)
    vid = db.execute("SELECT video_id FROM releases").fetchone()["video_id"]
    library.record_progress(db, mom, vid, 600, 600, ended=True)
    r = mom_client.post(f"/video/{vid}/unseen", data={"csrf_token": csrf_of(mom_client)})
    assert r.status_code == 302
    assert f"/watch/{vid}" in page(mom_client)
    assert mom_client.get(f"/watch/{vid}").status_code == 200
