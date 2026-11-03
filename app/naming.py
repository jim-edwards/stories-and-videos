"""Sorting and display-name helpers for folders and files."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"(\d+)")
# "01 - ", "2. ", "03_" at the start of a name: used to force an order, not part of the name.
_NUMBER_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*(?!\d)(?=\S)")


def natural_key(text: str) -> list:
    """Sort key where 'Part 2' comes before 'Part 10'. Even slots are text, odd slots are numbers."""
    return [int(part) if i % 2 else part.lower() for i, part in enumerate(_DIGITS.split(text))]


def strip_number_prefix(name: str) -> str:
    stripped = _NUMBER_PREFIX.sub("", name, count=1).strip()
    return stripped or name.strip()


def title_from_filename(stem: str) -> str:
    """Fallback title when the video has no title metadata."""
    name = strip_number_prefix(" ".join(stem.split()))
    return name if " " in name else name.replace("_", " ")


def story_title(folder_name: str) -> str:
    """Display name for a story folder ('' means videos placed loose in the top folder)."""
    if not folder_name:
        return "Other Videos"
    return strip_number_prefix(" ".join(folder_name.split()))


def nice_duration(seconds: float | None) -> str:
    """'12 minutes', '1 hour 5 minutes' - plain words, no clock notation."""
    if not seconds:
        return ""
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'' if hours == 1 else 's'}")
    if minutes:
        parts.append(f"{minutes} minute{'' if minutes == 1 else 's'}")
    return " ".join(parts)
