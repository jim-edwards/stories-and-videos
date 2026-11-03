import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "make-thumbnails.sh"

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("sh")), reason="needs ffmpeg")


def test_make_thumbnails_adds_only_the_missing_pictures(library_dir):
    (library_dir / "Spaced Out").mkdir()
    spaced = library_dir / "Spaced Out" / "A video with  spaces.mp4"
    shutil.copy(library_dir / "loose.mp4", spaced)
    kept = (library_dir / "Alpha Tales" / "Part 1.png").read_bytes()

    result = subprocess.run(["sh", str(SCRIPT), str(library_dir)], capture_output=True, text=True, check=True)

    assert (spaced.with_suffix(".jpg")).stat().st_size > 0                       # made, despite the spaces
    assert (library_dir / "loose.jpg").stat().st_size > 0
    assert (library_dir / "Alpha Tales" / "Part 1.png").read_bytes() == kept     # never replaced
    assert not (library_dir / "Alpha Tales" / "Part 1.jpg").exists()             # already had a .png
    assert not (library_dir / "Alpha Tales" / "Part 2.jpg").exists()             # already had a .JPG
    assert not (library_dir / ".hidden" / "secret.jpg").exists()
    assert not (library_dir / "@eaDir" / "thumb.jpg").exists()
    assert "0 failed" in result.stdout

    again = subprocess.run(["sh", str(SCRIPT), str(library_dir)], capture_output=True, text=True, check=True)
    assert "0 made" in again.stdout  # running again does nothing


def test_make_thumbnails_complains_about_a_bad_folder(tmp_path):
    result = subprocess.run(["sh", str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True)
    assert result.returncode == 1 and "Usage" in result.stderr
