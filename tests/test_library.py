from app import library
from conftest import video_id


def test_unseen_in_order_and_seen_split(db, mom):
    story = next(s for s in library.load_stories(db, mom) if s.folder == "Alpha Tales")
    assert [v.title for v in story.unseen] == ["One", "Two", "Ten"]
    assert story.seen == [] and not story.all_seen

    library.record_progress(db, mom, video_id(db, "One"), 2, 2, ended=True)
    story = next(s for s in library.load_stories(db, mom) if s.folder == "Alpha Tales")
    assert [v.title for v in story.unseen] == ["Two", "Ten"]
    assert [v.title for v in story.seen] == ["One"]


def test_stories_are_listed_by_folder_name_with_loose_videos_last(db, mom):
    # "05 - Gamma Story" sorts first: a number prefix is how you choose the order of stories.
    assert [s.title for s in library.load_stories(db, mom)] == ["Gamma Story", "Alpha Tales", "Beta", "Other Videos"]


def test_story_with_everything_watched_is_all_seen(db, mom):
    for title in ("First", "Second"):
        library.record_progress(db, mom, video_id(db, title), 0, 2, ended=True)
    beta = next(s for s in library.load_stories(db, mom) if s.folder == "Beta")
    assert beta.all_seen and beta.unseen == []


def test_partial_progress_is_remembered_but_not_watched(db, mom):
    vid = video_id(db, "Ten")
    assert library.record_progress(db, mom, vid, 1.0, 2.0, ended=False) is False
    video = library.get_video(db, mom, vid)
    assert not video.watched and video.position == 1.0


def test_resume_ignores_the_first_seconds_and_the_very_end():
    base = dict(id=1, story="", rel_path="a.mp4", title="t", description="", track=None, duration=600.0, watched=False)
    assert library.Video(position=3, **base).resume_at == 0
    assert library.Video(position=300, **base).resume_at == 300
    assert library.Video(position=300, **base).percent_done == 50
    assert library.Video(position=595, **base).resume_at == 0


def test_watched_at_ninety_percent_or_when_ended(db, mom):
    vid = video_id(db, "Two")
    assert library.record_progress(db, mom, vid, 1.7, 2.0, ended=False) is False  # 85%
    assert library.record_progress(db, mom, vid, 1.85, 2.0, ended=False) is True   # 92.5%
    other = video_id(db, "One")
    assert library.record_progress(db, mom, other, 0.1, 2.0, ended=True) is True


def test_server_uses_stored_duration_if_browser_sends_none(db, mom):
    assert library.record_progress(db, mom, video_id(db, "Two"), 1.95, 0, ended=False) is True


def test_once_watched_always_watched_even_if_replayed(db, mom):
    vid = video_id(db, "Two")
    library.record_progress(db, mom, vid, 2, 2, ended=True)
    assert library.record_progress(db, mom, vid, 0.5, 2, ended=False) is True
    video = library.get_video(db, mom, vid)
    assert video.watched and video.position == 0


def test_mark_unseen_puts_it_back(db, mom):
    vid = video_id(db, "Two")
    library.record_progress(db, mom, vid, 2, 2, ended=True)
    library.mark_unseen(db, mom, vid)
    assert not library.get_video(db, mom, vid).watched


def test_each_person_has_their_own_history(db, mom):
    from app import auth
    dad = auth.create_user(db, "dad", "dads password")
    library.record_progress(db, mom, video_id(db, "Two"), 2, 2, ended=True)
    assert library.get_video(db, mom, video_id(db, "Two")).watched
    assert not library.get_video(db, dad, video_id(db, "Two")).watched


def test_unknown_or_missing_video_is_reported(db, mom):
    assert library.record_progress(db, mom, 9999, 1, 2, ended=False) is None
    vid = video_id(db, "Two")
    db.execute("UPDATE videos SET missing = 1 WHERE id = ?", (vid,))
    assert library.record_progress(db, mom, vid, 1, 2, ended=False) is None


def test_pick_next_follows_order_skips_seen_and_wraps(db, mom):
    one, two, ten = (video_id(db, t) for t in ("One", "Two", "Ten"))
    videos = library.story_videos(db, mom, "Alpha Tales")
    assert library.pick_next(videos, one).id == two
    assert library.pick_next(videos, ten).id == one  # wraps round to what's still unseen

    library.record_progress(db, mom, two, 2, 2, ended=True)
    videos = library.story_videos(db, mom, "Alpha Tales")
    assert library.pick_next(videos, one).id == ten

    library.record_progress(db, mom, one, 2, 2, ended=True)
    library.record_progress(db, mom, ten, 2, 2, ended=True)
    videos = library.story_videos(db, mom, "Alpha Tales")
    assert library.pick_next(videos, ten) is None
