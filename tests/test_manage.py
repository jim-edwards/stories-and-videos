"""The administrator's controls for daily amounts, stories and the turnover time."""

import sqlite3

import pytest

from app import library, release
from app.config import Config
from app.db import SCHEMA, Database
from conftest import csrf_of, video_id
from test_views import page


def user_row(db, user_id):
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_of(client), **data})


# -- who may change things ------------------------------------------------------------------


def test_only_admins_can_open_or_change_a_persons_settings(mom_client, mom):
    token = csrf_of(mom_client)
    assert mom_client.get(f"/admin/users/{mom}").status_code == 403
    for path, data in ((f"/admin/users/{mom}/daily", {"mode": "daily", "minutes": "5"}),
                       (f"/admin/users/{mom}/stories", {"mode": "some", "story": "Beta"}),
                       ("/admin/release-time", {"release_time": "03:00"})):
        assert mom_client.post(path, data={"csrf_token": token, **data}).status_code == 403


def test_changes_need_the_page_token(admin_client, mom):
    assert admin_client.post(f"/admin/users/{mom}/daily", data={"mode": "daily", "minutes": "5"}).status_code == 400
    assert admin_client.post("/admin/release-time", data={"release_time": "03:00"}).status_code == 400


def test_an_unknown_person_is_a_404(admin_client):
    assert admin_client.get("/admin/users/9999").status_code == 404
    assert post(admin_client, "/admin/users/9999/daily", mode="all").status_code == 404
    assert post(admin_client, "/admin/users/9999/stories", mode="all").status_code == 404


# -- the daily amount -----------------------------------------------------------------------


def test_the_manage_page_links_to_each_persons_settings_and_summarises_them(admin_client, db, mom):
    html = page(admin_client, "/admin")
    assert f'href="/admin/users/{mom}"' in html and "all videos at once" in html and "all stories" in html
    release.set_daily_minutes(db, mom, 25)
    library.set_subscriptions(db, mom, {"Beta"})
    html = page(admin_client, "/admin")
    assert "25 minutes of new videos a day" in html and "1 story chosen" in html


def test_a_persons_page_shows_the_current_choices(admin_client, db, mom):
    html = page(admin_client, f"/admin/users/{mom}")
    assert "<h1>Mom</h1>" in html
    assert 'value="all" checked' in html                       # both forms start on "everything"
    assert html.count("checked") == 2
    assert "Gamma Story <span" in html and "05 - Gamma Story <span" not in html and "Other Videos <span" in html
    assert "(3 videos)" in html and "(1 video)" in html


def test_setting_a_daily_amount_releases_the_first_videos_straight_away(admin_client, db, mom):
    db.execute("UPDATE videos SET duration = 600")
    r = post(admin_client, f"/admin/users/{mom}/daily", mode="daily", minutes="30")
    assert r.status_code == 302 and r.headers["Location"] == f"/admin/users/{mom}"
    assert user_row(db, mom)["daily_minutes"] == 30
    assert db.execute("SELECT COUNT(*) FROM releases WHERE user_id = ?", (mom,)).fetchone()[0] == 3
    html = page(admin_client, f"/admin/users/{mom}")
    assert 'value="30"' in html and "Waiting to be watched" in html and "30 minutes" in html
    assert "Held back for later:</strong> 4 videos" in html and "Next release" in html


def test_switching_the_daily_amount_off(admin_client, db, mom):
    release.set_daily_minutes(db, mom, 30)
    post(admin_client, f"/admin/users/{mom}/daily", mode="all")
    assert user_row(db, mom)["daily_minutes"] is None


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "1.5", "601", "99999999", " "])
def test_a_daily_amount_that_makes_no_sense_is_explained_and_changes_nothing(admin_client, db, mom, bad):
    release.set_daily_minutes(db, mom, 30)
    r = post(admin_client, f"/admin/users/{mom}/daily", mode="daily", minutes=bad)
    assert r.status_code == 302
    assert user_row(db, mom)["daily_minutes"] == 30
    page_html = page(admin_client, f"/admin/users/{mom}")
    assert "alert-error" in page_html and ("minutes" in page_html)


# -- stories --------------------------------------------------------------------------------


def test_choosing_stories_for_a_person(admin_client, db, mom):
    r = post(admin_client, f"/admin/users/{mom}/stories", mode="some", story=["Beta", ""])
    assert r.status_code == 302
    assert user_row(db, mom)["subscribe_all"] == 0
    assert library.subscribed_stories(db, mom) == {"Beta", ""}
    html = page(admin_client, f"/admin/users/{mom}")
    assert 'name="mode" value="some" checked' in html
    assert 'name="story" value="Beta" checked' in html and 'name="story" value="" checked' in html
    assert 'name="story" value="Alpha Tales" checked' not in html
    assert [s.title for s in library.load_stories(db, mom)] == ["Beta", "Other Videos"]


def test_choosing_all_stories_again(admin_client, db, mom):
    post(admin_client, f"/admin/users/{mom}/stories", mode="some", story="Beta")
    post(admin_client, f"/admin/users/{mom}/stories", mode="all")
    assert user_row(db, mom)["subscribe_all"] == 1
    assert len(library.load_stories(db, mom)) == 4


def test_choosing_some_but_ticking_nothing_gives_an_empty_list(admin_client, db, mom):
    post(admin_client, f"/admin/users/{mom}/stories", mode="some")
    assert library.load_stories(db, mom) == []


def test_changing_stories_tops_up_a_persons_daily_release_from_the_new_ones(admin_client, db, mom):
    db.execute("UPDATE videos SET duration = 600")
    release.set_daily_minutes(db, mom, 20)
    post(admin_client, f"/admin/users/{mom}/stories", mode="some", story="Beta")
    assert [v.title for s in library.load_stories(db, mom) for v in s.videos] == ["First", "Second"]


# -- when the day turns over ----------------------------------------------------------------


def test_the_manage_page_shows_and_changes_the_turnover_time(admin_client, db):
    html = page(admin_client, "/admin")
    assert 'value="01:00"' in html and "UTC" in html and "TZ" in html
    r = post(admin_client, "/admin/release-time", release_time="06:30")
    assert r.status_code == 302
    assert release.get_release_time(db).strftime("%H:%M") == "06:30"
    assert 'value="06:30"' in page(admin_client, "/admin")


def test_a_bad_turnover_time_is_explained_and_changes_nothing(admin_client, db):
    post(admin_client, "/admin/release-time", release_time="26:99")
    assert release.get_release_time(db).strftime("%H:%M") == "01:00"
    assert "hours and minutes" in page(admin_client, "/admin")


def test_the_page_names_the_configured_time_zone(app, admin_client):
    sv = app.extensions["sv"]
    from dataclasses import replace
    sv.cfg = replace(sv.cfg, timezone_name="America/Chicago")
    html = page(admin_client, "/admin")
    assert "<strong>America/Chicago</strong> time" in html and "set the <code>TZ</code>" not in html


# -- older databases and the time zone setting ----------------------------------------------


def test_a_database_from_before_daily_releases_is_upgraded_and_everyone_still_sees_everything(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA)
    for name in ("subscriptions", "releases", "release_days", "settings"):
        old.execute(f"DROP TABLE {name}")
    old.execute("ALTER TABLE users DROP COLUMN daily_minutes")
    old.execute("ALTER TABLE users DROP COLUMN subscribe_all")
    old.execute("INSERT INTO users (username, display_name, password_hash, created_at) VALUES ('mom', 'Mom', 'x', 1)")
    old.execute("INSERT INTO videos (story, rel_path, title, size, mtime_ns, added_at) VALUES ('S', 'S/a.mp4', 'A', 1, 2, 3)")
    old.commit()
    old.close()

    Database(path)
    Database(path)  # running again is harmless
    conn = Database(path).connect()
    mom = conn.execute("SELECT id, daily_minutes, subscribe_all FROM users").fetchone()
    assert mom["daily_minutes"] is None and mom["subscribe_all"] == 1
    assert [s.title for s in library.load_stories(conn, mom["id"])] == ["S"]
    assert release.get_release_time(conn).strftime("%H:%M") == "01:00"


def test_the_time_zone_comes_from_tz(monkeypatch, caplog):
    zoneinfo = pytest.importorskip("zoneinfo")
    monkeypatch.delenv("TZ", raising=False)
    assert Config.from_env().timezone_name == "UTC"
    try:
        zoneinfo.ZoneInfo("America/Chicago")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no time zone database on this machine")
    monkeypatch.setenv("TZ", "America/Chicago")
    cfg = Config.from_env()
    assert cfg.timezone_name == "America/Chicago" and cfg.timezone.key == "America/Chicago"


@pytest.mark.parametrize("bad", ["Not/AZone", "../../etc/passwd", "   "])
def test_an_unknown_time_zone_falls_back_to_utc(monkeypatch, caplog, bad):
    monkeypatch.setenv("TZ", bad)
    cfg = Config.from_env()
    assert cfg.timezone_name == "UTC" and cfg.timezone.utcoffset(None).total_seconds() == 0
    if bad.strip():
        assert "not a time zone" in caplog.text


def test_story_names_with_quotes_and_ampersands_survive_the_round_trip(admin_client, db, mom):
    from html import unescape
    import re
    name = "Grandma's & Grandpa's \"Best\""
    db.execute("INSERT INTO videos (story, rel_path, title, size, mtime_ns, added_at) VALUES (?, ?, 'x', 1, 1, 1)",
               (name, f"{name}/x.mp4"))
    html = page(admin_client, f"/admin/users/{mom}")
    values = [unescape(v) for v in re.findall(r'name="story" value="([^"]*)"', html)]
    assert name in values and "Grandma's &" not in html                       # escaped in the page
    post(admin_client, f"/admin/users/{mom}/stories", mode="some", story=[name])  # what a browser sends back
    assert library.subscribed_stories(db, mom) == {name}
    assert 'name="story" value="Grandma&#39;s &amp; Grandpa&#39;s &#34;Best&#34;" checked' in page(admin_client, f"/admin/users/{mom}")
