"""Settings, read from environment variables so the container needs no config file."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

# Formats browsers can (usually) play without conversion. H.264 MP4 is the safest.
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ogv"})
# A picture saved next to a video (same name, one of these extensions) is its thumbnail.
# If there is more than one, the earlier extension wins.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, but it is {raw!r}")


def _timezone_from_env() -> tuple[tzinfo, str]:
    """The time zone named by TZ (for example America/Chicago), or UTC if it is unset or unknown."""
    name = os.environ.get("TZ", "").strip()
    if not name:
        return timezone.utc, "UTC"
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        log.warning("TZ=%r is not a time zone this system knows (try something like America/Chicago); using UTC.", name)
        return timezone.utc, "UTC"


@dataclass(frozen=True)
class Config:
    video_dir: Path
    data_dir: Path
    # Seconds between automatic scans of the video folder.
    scan_interval: int = 300
    # Files modified more recently than this are assumed to still be copying in.
    settle_seconds: int = 30
    # How long a sign-in lasts (it renews itself whenever the site is used).
    session_days: int = 365
    # Number of reverse proxies in front of the app (trusts X-Forwarded-* headers).
    trust_proxy: int = 0
    # First-run admin account, only used when no users exist yet.
    admin_username: str = "admin"
    admin_password: str | None = None
    ffprobe: str = "ffprobe"
    site_name: str = "Stories and Videos"
    # The clock that "tomorrow" is measured on, for the daily release of new videos.
    timezone: tzinfo = timezone.utc
    timezone_name: str = "UTC"

    @classmethod
    def from_env(cls) -> "Config":
        zone, zone_name = _timezone_from_env()
        return cls(
            video_dir=Path(os.environ.get("VIDEO_DIR", "/videos")),
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            scan_interval=max(10, _int_env("SCAN_INTERVAL", 300)),
            settle_seconds=max(0, _int_env("SETTLE_SECONDS", 30)),
            session_days=max(1, _int_env("SESSION_DAYS", 365)),
            trust_proxy=max(0, _int_env("TRUST_PROXY", 0)),
            admin_username=os.environ.get("ADMIN_USERNAME", "").strip() or "admin",
            admin_password=os.environ.get("ADMIN_PASSWORD") or None,
            ffprobe=os.environ.get("FFPROBE", "ffprobe"),
            timezone=zone,
            timezone_name=zone_name,
        )
