import os
import shutil
import time

from app import scanner as scanner_mod


def titles(db, story):
    from app import library
    return [v.title for v in library.story_videos(db, 0, story)]


def test_reads_title_and_description_from_the_files(db):
    row = db.execute("SELECT * FROM videos WHERE title = 'Two'").fetchone()
    assert row["description"] == "The second part."
    assert row["story"] == "Alpha Tales"
    assert row["rel_path"] == "Alpha Tales/Part 2.mp4"
    assert 1.5 < row["duration"] < 2.5


def test_missing_title_falls_back_to_filename(db):
    row = db.execute("SELECT * FROM videos WHERE rel_path LIKE '05 - Gamma Story/%'").fetchone()
    assert row["title"] == "the last day"
    assert row["description"] == ""


def test_orders_by_file_name_naturally_and_by_track_when_present(db):
    assert titles(db, "Alpha Tales") == ["One", "Two", "Ten"]
    assert titles(db, "Beta") == ["First", "Second"]


def test_loose_files_go_in_their_own_group(db):
    assert titles(db, "") == ["A Loose Video"]


def test_ignores_hidden_folders_nas_junk_and_non_videos(db):
    paths = [r["rel_path"] for r in db.execute("SELECT rel_path FROM videos")]
    assert len(paths) == 7
    assert not any(p.startswith((".hidden", "@eaDir")) or p.endswith(".txt") for p in paths)


def test_rescan_does_not_reread_unchanged_files(services, monkeypatch):
    calls = []
    monkeypatch.setattr(scanner_mod, "probe_video", lambda *a: calls.append(a))
    services.scanner.scan_once()
    assert calls == []


def test_new_file_is_picked_up_and_changed_file_is_reread(services, db, library_dir, sample_videos):
    shutil.copy(sample_videos / "Beta" / "a.mp4", library_dir / "Beta" / "c.mp4")
    old = time.time() - 3600
    os.utime(library_dir / "Beta" / "c.mp4", (old, old))
    status = services.scanner.scan_once()
    assert status.added == 1 and status.updated == 0
    assert db.execute("SELECT COUNT(*) FROM videos WHERE story = 'Beta'").fetchone()[0] == 3

    # Replace a file with different content: same path, same id, new metadata.
    before = db.execute("SELECT id FROM videos WHERE title = 'One'").fetchone()["id"]
    shutil.copy(sample_videos / "loose.mp4", library_dir / "Alpha Tales" / "Part 1.mp4")
    os.utime(library_dir / "Alpha Tales" / "Part 1.mp4", (old + 5, old + 5))
    status = services.scanner.scan_once()
    assert status.updated == 1
    row = db.execute("SELECT id, title FROM videos WHERE rel_path = 'Alpha Tales/Part 1.mp4'").fetchone()
    assert row["id"] == before and row["title"] == "A Loose Video"


def test_file_still_being_copied_is_left_for_later(services, db, library_dir, sample_videos):
    shutil.copy(sample_videos / "Beta" / "a.mp4", library_dir / "Beta" / "new.mp4")  # mtime = now
    status = services.scanner.scan_once()
    assert status.waiting == 1 and status.added == 0
    assert db.execute("SELECT COUNT(*) FROM videos WHERE rel_path = 'Beta/new.mp4'").fetchone()[0] == 0


def test_unreadable_video_is_reported_not_added(services, db, library_dir):
    bad = library_dir / "Beta" / "broken.mp4"
    bad.write_bytes(b"this is not a video" * 10)
    old = time.time() - 3600
    os.utime(bad, (old, old))
    status = services.scanner.scan_once()
    assert status.unreadable == ["Beta/broken.mp4"]
    assert db.execute("SELECT COUNT(*) FROM videos WHERE rel_path = 'Beta/broken.mp4'").fetchone()[0] == 0
    # ...and it isn't re-probed every time.
    assert services.scanner.scan_once().unreadable == []


def test_removed_file_is_hidden_but_remembered_and_comes_back(services, db, library_dir):
    from app import library
    target = library_dir / "Alpha Tales" / "Part 2.mp4"
    moved_away = library_dir.parent / "parked.mp4"
    shutil.move(target, moved_away)
    services.scanner.scan_once()
    row = db.execute("SELECT id, missing FROM videos WHERE title = 'Two'").fetchone()
    assert row["missing"] == 1
    assert titles(db, "Alpha Tales") == ["One", "Ten"]

    shutil.move(moved_away, target)
    services.scanner.scan_once()
    assert db.execute("SELECT missing FROM videos WHERE id = ?", (row["id"],)).fetchone()["missing"] == 0
    assert titles(db, "Alpha Tales") == ["One", "Two", "Ten"]


def test_moved_or_renamed_file_keeps_its_identity_and_watch_history(services, db, mom, library_dir):
    from app import library
    vid = db.execute("SELECT id FROM videos WHERE title = 'Two'").fetchone()["id"]
    library.record_progress(db, mom, vid, 2, 2, ended=True)

    (library_dir / "New Folder").mkdir()
    os.rename(library_dir / "Alpha Tales" / "Part 2.mp4", library_dir / "New Folder" / "Renamed.mp4")
    status = services.scanner.scan_once()

    assert status.moved == 1 and status.added == 0
    row = db.execute("SELECT * FROM videos WHERE id = ?", (vid,)).fetchone()
    assert row["rel_path"] == "New Folder/Renamed.mp4" and row["story"] == "New Folder" and row["missing"] == 0
    assert library.get_video(db, mom, vid).watched


def test_missing_video_folder_is_reported_and_keeps_data(services, db, library_dir):
    shutil.rmtree(library_dir)
    status = services.scanner.scan_once()
    assert "not found" in status.error
    assert db.execute("SELECT COUNT(*) FROM videos WHERE missing = 0").fetchone()[0] == 7


def thumbs(db):
    return {r["rel_path"]: r["thumb"] for r in db.execute("SELECT rel_path, thumb FROM videos")}


def test_pictures_next_to_videos_are_matched_by_name(db):
    assert thumbs(db) == {
        "Alpha Tales/Part 1.mp4": "Alpha Tales/Part 1.png",
        "Alpha Tales/Part 2.mp4": "Alpha Tales/Part 2.JPG",   # extension in any letter case
        "Alpha Tales/Part 10.mp4": None,                       # empty picture file is ignored
        "Beta/a.mp4": "Beta/a.jpeg",
        "Beta/b.mp4": "Beta/b.jpg",                            # .jpg is preferred over .png
        "05 - Gamma Story/03_the_last_day.mp4": None,
        "loose.mp4": None,
    }


def test_pictures_are_not_mistaken_for_videos(db):
    assert db.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 7


def test_adding_or_removing_a_picture_needs_no_video_rescan(services, db, library_dir, sample_videos, monkeypatch):
    calls = []
    monkeypatch.setattr(scanner_mod, "probe_video", lambda *a: calls.append(a))
    target = library_dir / "loose.png"
    shutil.copy(sample_videos / "Beta" / "b.png", target)
    services.scanner.scan_once()
    assert thumbs(db)["loose.mp4"] == "loose.png"

    target.unlink()
    services.scanner.scan_once()
    assert thumbs(db)["loose.mp4"] is None
    assert calls == []  # the videos were never re-read


def test_moving_a_video_without_its_picture_drops_the_picture(services, db, library_dir):
    os.rename(library_dir / "Beta" / "a.mp4", library_dir / "Beta" / "renamed.mp4")
    services.scanner.scan_once()
    row = db.execute("SELECT thumb FROM videos WHERE rel_path = 'Beta/renamed.mp4'").fetchone()
    assert row["thumb"] is None
