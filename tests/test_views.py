import re

from app import library
from conftest import csrf_of, video_id


def page(client, path="/"):
    r = client.get(path)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def story_html(html, title):
    match = re.search(rf'<section class="story[^"]*" id="story-\d+" aria-labelledby="story-title-\d+">\s*<header class="story-head">\s*<h2[^>]*>{re.escape(title)}</h2>.*?</section>', html, re.S)
    assert match, f"story {title!r} not on page"
    return match.group(0)


def test_home_greets_by_name_and_lists_unseen_videos_in_order(mom_client):
    html = page(mom_client)
    assert "Hello, Mom" in html
    alpha = story_html(html, "Alpha Tales")
    assert alpha.index(">One<") < alpha.index(">Two<") < alpha.index(">Ten<")
    assert "Up next" in alpha and "3 to watch" in alpha
    assert "The first part." in alpha  # description shown
    assert "Already seen" not in alpha  # nothing watched yet


def test_home_titles_hide_folder_number_prefix_and_show_other_videos(mom_client):
    html = page(mom_client)
    assert "Gamma Story" in html and "05 - Gamma" not in html
    assert "Other Videos" in html


def test_seen_videos_move_under_already_seen_and_are_collapsed(mom_client, db, mom):
    library.record_progress(db, mom, video_id(db, "One"), 2, 2, ended=True)
    alpha = story_html(page(mom_client), "Alpha Tales")
    unseen_part, seen_part = alpha.split('<details class="seen"')
    assert ">Two<" in unseen_part and ">One<" not in unseen_part
    assert "Already seen (1)" in seen_part and "One" in seen_part
    assert seen_part.startswith(">") and " open" not in seen_part.split(">")[0]  # collapsed by default
    assert "2 to watch" in alpha and "Watch again" in seen_part


def test_fully_watched_story_is_greyed_and_says_so(mom_client, db, mom):
    for title in ("First", "Second"):
        library.record_progress(db, mom, video_id(db, title), 2, 2, ended=True)
    html = page(mom_client)
    beta = story_html(html, "Beta")
    assert 'class="story done"' in beta and "You have seen all of these" in beta
    assert "to watch" not in beta and "Already seen (2)" in beta
    assert 'class="story done"' not in story_html(html, "Alpha Tales")


def test_long_story_shows_a_few_then_offers_more(mom_client, db, library_dir, sample_videos):
    import os, shutil, time
    for n in range(4):
        dest = library_dir / "Alpha Tales" / f"Part {20 + n}.mp4"
        shutil.copy(sample_videos / "loose.mp4", dest)
        os.utime(dest, (time.time() - 3600 + n, time.time() - 3600 + n))
    mom_client.application.extensions["sv"].scanner.scan_once()
    alpha = story_html(page(mom_client), "Alpha Tales")
    assert "7 to watch" in alpha and "Show 4 more" in alpha


def test_in_progress_video_offers_continue(mom_client, db, mom):
    vid = video_id(db, "Ten")
    db.execute("UPDATE videos SET duration = 600 WHERE id = ?", (vid,))
    library.record_progress(db, mom, vid, 300, 600, ended=False)
    html = story_html(page(mom_client), "Alpha Tales")
    assert "Continue" in html and "You stopped part-way" in html and 'value="50"' in html


def test_mark_unseen_returns_to_the_open_list(mom_client, db, mom):
    vid = video_id(db, "One")
    library.record_progress(db, mom, vid, 2, 2, ended=True)
    r = mom_client.post(f"/video/{vid}/unseen", data={"csrf_token": csrf_of(mom_client)})
    # Alpha Tales is the second story on the page (after "05 - Gamma Story").
    assert r.status_code == 302 and r.headers["Location"] == "/?open=2#story-2"
    assert not library.get_video(db, mom, vid).watched


def test_empty_library_has_a_friendly_message(mom_client, db):
    db.execute("UPDATE videos SET missing = 1")
    assert "no stories here yet" in page(mom_client)


def test_watch_page_has_player_description_and_next_button(mom_client, db):
    html = page(mom_client, f"/watch/{video_id(db, 'One')}")
    assert "<h1>One</h1>" in html and "The first part." in html
    assert f'src="/media/{video_id(db, "One")}"' in html
    assert f"/watch/{video_id(db, 'Two')}" in html and "Next: Two" in html
    assert "All my stories" in html


def test_watch_page_for_last_video_has_no_next_button(mom_client, db, mom):
    for t in ("One", "Two"):
        library.record_progress(db, mom, video_id(db, t), 2, 2, ended=True)
    html = page(mom_client, f"/watch/{video_id(db, 'Ten')}")
    assert "Next:" not in html and "seen everything in" in html


def test_watch_unknown_video_is_a_friendly_404(mom_client):
    r = mom_client.get("/watch/9999")
    assert r.status_code == 404 and "couldn&#39;t find that" in r.get_data(as_text=True)


def test_media_streams_whole_file_and_byte_ranges(mom_client, db, library_dir):
    vid = video_id(db, "Two")
    full = (library_dir / "Alpha Tales" / "Part 2.mp4").read_bytes()
    r = mom_client.get(f"/media/{vid}")
    assert r.status_code == 200 and r.data == full and r.headers["Content-Type"] == "video/mp4"
    assert r.headers["Accept-Ranges"] == "bytes" and "private" in r.headers["Cache-Control"]

    r = mom_client.get(f"/media/{vid}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.data == full[10:20]
    assert r.headers["Content-Range"] == f"bytes 10-19/{len(full)}"


def test_media_for_missing_video_or_file_is_404(mom_client, db, library_dir):
    assert mom_client.get("/media/9999").status_code == 404
    vid = video_id(db, "Two")
    (library_dir / "Alpha Tales" / "Part 2.mp4").unlink()  # vanished since the last scan
    assert mom_client.get(f"/media/{vid}").status_code == 404


def test_progress_api_marks_watched_and_returns_state(mom_client, db, mom):
    vid = video_id(db, "Two")
    headers = {"X-CSRF-Token": csrf_of(mom_client)}
    r = mom_client.post(f"/api/progress/{vid}", json={"position": 0.5, "duration": 2.0}, headers=headers)
    assert r.get_json() == {"watched": False}
    r = mom_client.post(f"/api/progress/{vid}", json={"position": 2.0, "duration": 2.0, "ended": True}, headers=headers)
    assert r.get_json() == {"watched": True}
    assert library.get_video(db, mom, vid).watched


def test_progress_api_validates_input(mom_client):
    headers = {"X-CSRF-Token": csrf_of(mom_client)}
    assert mom_client.post("/api/progress/1", json={"position": 1}).status_code == 400  # no token
    assert mom_client.post("/api/progress/1", data="nope", headers=headers).status_code == 400
    assert mom_client.post("/api/progress/1", json={"position": "x"}, headers=headers).status_code == 400
    assert mom_client.post("/api/progress/1", json={"position": float("inf")}, headers=headers).status_code == 400
    assert mom_client.post("/api/progress/9999", json={"position": 1}, headers=headers).status_code == 404


def test_security_headers_and_no_inline_script_needed(mom_client):
    r = mom_client.get("/")
    assert "script-src" not in r.headers["Content-Security-Policy"]  # default-src 'self' covers it
    assert "'unsafe-inline'" not in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    html = r.get_data(as_text=True)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and " onclick=" not in html and " style=" not in html


def test_descriptions_from_files_are_escaped(mom_client, db):
    db.execute("UPDATE videos SET description = '<script>alert(1)</script>' WHERE title = 'One'")
    html = page(mom_client)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_cards_show_the_picture_beside_the_text_only_when_there_is_one(mom_client, db):
    html = page(mom_client)
    alpha = story_html(html, "Alpha Tales")
    one = video_id(db, "One")
    assert f'<img src="/thumb/{one}"' in alpha and "has-thumb" in alpha
    ten = story_html(html, "Alpha Tales").split(">Ten<")[0].rsplit("<article", 1)[1]
    assert "has-thumb" not in ten and "<img" not in ten  # the card for "Ten" has no picture
    assert "<img" not in story_html(html, "Other Videos")


def test_seen_list_and_player_use_the_picture_too(mom_client, db, mom):
    one = video_id(db, "One")
    library.record_progress(db, mom, one, 2, 2, ended=True)
    seen = story_html(page(mom_client), "Alpha Tales").split('<details class="seen"')[1]
    assert f'class="seen-thumb" src="/thumb/{one}"' in seen
    assert f'poster="/thumb/{one}"' in page(mom_client, f"/watch/{one}")
    assert "poster=" not in page(mom_client, f"/watch/{video_id(db, 'Ten')}")


def test_thumb_route_serves_the_right_file_and_type(mom_client, db, library_dir):
    png = mom_client.get(f"/thumb/{video_id(db, 'One')}")
    assert png.status_code == 200 and png.headers["Content-Type"] == "image/png"
    assert png.data == (library_dir / "Alpha Tales" / "Part 1.png").read_bytes()
    assert "private" in png.headers["Cache-Control"]
    jpg = mom_client.get(f"/thumb/{video_id(db, 'Two')}")  # stored as Part 2.JPG
    assert jpg.status_code == 200 and jpg.headers["Content-Type"] == "image/jpeg"
    assert mom_client.get(f"/thumb/{video_id(db, 'First')}").headers["Content-Type"] == "image/jpeg"


def test_thumb_route_404s_when_there_is_no_picture_or_it_vanished(mom_client, db, library_dir):
    assert mom_client.get(f"/thumb/{video_id(db, 'Ten')}").status_code == 404  # none exists
    assert mom_client.get("/thumb/9999").status_code == 404
    (library_dir / "Alpha Tales" / "Part 1.png").unlink()                       # gone since the last scan
    assert mom_client.get(f"/thumb/{video_id(db, 'One')}").status_code == 404


def test_thumb_route_needs_sign_in(client, db):
    r = client.get(f"/thumb/{video_id(db, 'One')}")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_manage_page_counts_videos_with_pictures(admin_client):
    html = page(admin_client, "/admin")
    assert "4 of 7" in html and "same name" in html
