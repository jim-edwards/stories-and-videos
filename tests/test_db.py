import sqlite3

from app.db import SCHEMA, Database

OLD_VIDEOS_TABLE = SCHEMA.split("CREATE TABLE IF NOT EXISTS videos (")[1].split(");")[0]


def test_database_from_before_pictures_gets_the_new_column_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA.replace("    thumb       TEXT,                      -- path of the picture saved next to the video, if any\n", ""))
    assert "thumb" not in [r[1] for r in old.execute("PRAGMA table_info(videos)")]
    old.execute("INSERT INTO videos (story, rel_path, title, size, mtime_ns, added_at) VALUES ('S', 'S/a.mp4', 'A', 1, 2, 3)")
    old.commit()
    old.close()

    Database(path)
    Database(path)  # running again is harmless

    conn = Database(path).connect()
    assert "thumb" in [r["name"] for r in conn.execute("PRAGMA table_info(videos)")]
    row = conn.execute("SELECT title, thumb FROM videos").fetchone()
    assert row["title"] == "A" and row["thumb"] is None
