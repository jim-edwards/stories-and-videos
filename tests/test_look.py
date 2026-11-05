"""How the pages are dressed: pictures of any shape, and the light and dark colors.

The behaviour itself (a picture shown whole, the dark colors appearing) can only be seen in a browser,
so these tests guard the rules in the stylesheet and page markup that make it work.
"""

import re
from pathlib import Path

import app as app_package
from test_views import page

STATIC = Path(app_package.__file__).parent / "static"
CSS = (STATIC / "style.css").read_text()
JS = (STATIC / "prefs.js").read_text()


def rule(selector: str) -> str:
    """The declarations of the first rule whose selector list is exactly `selector`."""
    match = re.search(r"(?:^|[}/])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector!r}"
    return match.group(1)


def variables(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def dark_palettes() -> tuple[dict[str, str], dict[str, str]]:
    """The dark colors as used when the device is dark, and as used when 'Dark' was chosen."""
    by_device = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([^}]*)\}', CSS)
    by_choice = re.search(r':root\[data-theme="dark"\] \{([^}]*)\}', CSS)
    assert by_device and by_choice
    return variables(by_device.group(1)), variables(by_choice.group(1))


# -- pictures of any shape ------------------------------------------------------------------------


def test_pictures_are_scaled_to_fit_never_cropped():
    assert "object-fit: cover" not in CSS
    assert "aspect-ratio: 16" not in CSS  # not forced to one shape
    card_picture = rule(".thumb img")
    assert "object-fit: contain" in card_picture
    assert "max-height" in card_picture      # a tall picture is limited in height...
    assert "max-width: 100%" in card_picture  # ...and a wide one in width, each keeping its shape
    assert "width: auto" in card_picture and "height: auto" in card_picture


def test_seen_list_pictures_are_shown_whole_in_a_column_of_equal_width():
    assert "max-height" in rule(".seen-thumb") and "max-width: 100%" in rule(".seen-thumb")
    assert "flex: 0 0 6rem" in rule(".seen-pic")


def test_seen_list_wraps_each_picture_in_its_column(mom_client, db, mom):
    from app import library
    from conftest import video_id
    one = video_id(db, "One")
    library.record_progress(db, mom, one, 2, 2, ended=True)
    assert f'<span class="seen-pic"><img class="seen-thumb" src="/thumb/{one}"' in page(mom_client)


# -- light and dark ---------------------------------------------------------------------------------


def test_the_dark_colors_are_the_same_whether_the_device_or_the_person_chose_them():
    by_device, by_choice = dark_palettes()
    assert by_device.pop("color-scheme", None) is None  # color-scheme is not a --variable
    assert by_device == by_choice and by_device


def test_the_dark_palette_covers_every_color_the_light_one_defines():
    light = variables(re.search(r":root \{([^}]*)\}", CSS).group(1))
    dark, _ = dark_palettes()
    colors = {name for name, value in light.items() if value.strip().startswith("#")}
    assert colors and colors <= set(dark), f"missing from dark: {sorted(colors - set(dark))}"


def test_no_color_is_written_into_a_rule_so_every_color_changes_with_the_theme():
    outside = re.sub(r"(:root[^{]*\{[^}]*\})", "", CSS)  # drop the palettes themselves
    colors = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:white|black)\b(?!-)", outside)
    # The picture area of the video player is black in either theme, and its dimmer is a shade of black.
    assert sorted(colors) == ["#000", "#000", "rgba(0, 0, 0, .4)"]


def test_both_themes_can_be_told_apart_by_the_browser():
    assert 'name="color-scheme"' not in CSS  # (that is a <meta> tag, not CSS)
    assert "color-scheme: light" in rule(":root")
    assert len(re.findall(r"(?<![-\w])color-scheme: dark", CSS)) == 2  # once for each way of being dark


def test_signed_in_pages_offer_auto_light_and_dark_at_the_top(mom_client, admin_client):
    for client, path in ((mom_client, "/"), (admin_client, "/admin")):
        html = page(client, path)
        buttons = re.findall(r'data-set-theme="(\w+)"', html)
        assert buttons == ["auto", "light", "dark"]
        assert '<meta name="color-scheme" content="light dark">' in html


def test_the_choice_is_remembered_under_one_key_and_never_forced_on_people_who_made_none():
    assert JS.count('"sv-theme"') == 1
    assert "data-theme" in JS and "prefers-color-scheme: dark" in JS
    # With nothing saved the attribute is removed, so the stylesheet follows the device by itself.
    assert 'removeAttribute("data-theme")' in JS


def test_auto_forgets_the_choice_so_the_device_decides_again():
    assert "localStorage.removeItem(THEME_KEY)" in JS
    assert '|| "auto"' in JS  # with nothing saved, Auto is the button shown as pressed
