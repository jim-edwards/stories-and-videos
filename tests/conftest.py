import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app import create_app
from app.config import Config

ADMIN = ("boss", "correct horse battery")
MOM = ("mom", "sunny garden walk")


def _make_video(path: Path, title=None, description=None, track=None, seconds=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=10:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}", "-c:v", "mpeg4", "-c:a", "aac", "-shortest"]
    for key, value in (("title", title), ("description", description), ("track", track)):
        if value is not None:
            cmd += ["-metadata", f"{key}={value}"]
    subprocess.run(cmd + [str(path)], check=True)


def _make_picture(path: Path, colour="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=32x32",
                    "-frames:v", "1", str(path)], check=True)


@pytest.fixture(scope="session")
def sample_videos(tmp_path_factory) -> Path:
    """A small library covering the ordering and naming rules. Built once, copied per test."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to build test videos")
    root = tmp_path_factory.mktemp("library")
    # Natural filename order (Part 2 before Part 10), no track numbers.
    _make_video(root / "Alpha Tales" / "Part 10.mp4", "Ten", "The tenth part.")
    _make_video(root / "Alpha Tales" / "Part 2.mp4", "Two", "The second part.")
    _make_video(root / "Alpha Tales" / "Part 1.mp4", "One", "The first part.")
    # Track numbers beat file names.
    _make_video(root / "Beta" / "a.mp4", "Second", "Has track 2/2.", track="2/2")
    _make_video(root / "Beta" / "b.mp4", "First", "Has track 1/2.", track="1/2")
    # No metadata at all: title comes from the file name; folder number prefix is hidden.
    _make_video(root / "05 - Gamma Story" / "03_the_last_day.mp4")
    _make_video(root / "loose.mp4", "A Loose Video", "Not in any folder.")
    # Things that must be ignored.
    (root / "Alpha Tales" / "notes.txt").write_text("not a video")
    _make_video(root / ".hidden" / "secret.mp4", "Secret")
    _make_video(root / "@eaDir" / "thumb.mp4", "Synology junk")
    # Pictures saved next to videos (same name; .jpg/.jpeg/.png, any letter case).
    _make_picture(root / "Alpha Tales" / "Part 1.png")
    _make_picture(root / "Alpha Tales" / "Part 2.JPG")
    (root / "Alpha Tales" / "Part 10.jpg").write_bytes(b"")           # empty file: not a picture
    _make_picture(root / "Alpha Tales" / "._Part 1.png")               # macOS leftover: ignored
    _make_picture(root / "Beta" / "a.jpeg")
    _make_picture(root / "Beta" / "b.png")
    _make_picture(root / "Beta" / "b.jpg")                             # .jpg beats .png
    _make_picture(root / "Beta" / "unrelated.png")                     # no video with that name
    old = time.time() - 3600
    for path in root.rglob("*"):
        os.utime(path, (old, old))
    return root


@pytest.fixture
def library_dir(sample_videos, tmp_path) -> Path:
    dest = tmp_path / "videos"
    shutil.copytree(sample_videos, dest)
    return dest


@pytest.fixture
def cfg(library_dir, tmp_path) -> Config:
    return Config(video_dir=library_dir, data_dir=tmp_path / "data", settle_seconds=5,
                  admin_username=ADMIN[0], admin_password=ADMIN[1])


@pytest.fixture
def app(cfg):
    app = create_app(cfg, start_scanner=False)
    app.config["TESTING"] = True
    app.extensions["sv"].scanner.scan_once()
    return app


@pytest.fixture
def services(app):
    return app.extensions["sv"]


@pytest.fixture
def db(services):
    conn = services.db.connect()
    yield conn
    conn.close()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, creds, **kwargs):
    return client.post("/login", data={"username": creds[0], "password": creds[1]}, **kwargs)


@pytest.fixture
def admin_client(client):
    assert login(client, ADMIN).status_code == 302
    return client


@pytest.fixture
def mom(services, db):
    """A normal (non-admin) account, returned as its user id."""
    from app import auth
    return auth.create_user(db, MOM[0], MOM[1], "Mom")


@pytest.fixture
def mom_client(app, mom):
    c = app.test_client()
    assert login(c, MOM).status_code == 302
    return c


def csrf_of(client) -> str:
    """The CSRF token for the signed-in session, read from any rendered page."""
    import re
    page = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def video_id(db, title):
    return db.execute("SELECT id FROM videos WHERE title = ?", (title,)).fetchone()["id"]
