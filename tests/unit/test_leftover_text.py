from __future__ import annotations

from artifact_skill.leftover_text import MARKERS, find_leftover_markers


def test_finds_a_known_marker():
    assert find_leftover_markers("Lorem ipsum dolor sit amet") == ["lorem ipsum"]


def test_case_insensitive():
    assert find_leftover_markers("LOREM IPSUM") == ["lorem ipsum"]
    assert find_leftover_markers("Click To Add Title") == ["click to add"]


def test_finds_multiple_markers_in_order():
    text = "TODO: replace this. Lorem ipsum filler content. FIXME later."
    found = find_leftover_markers(text)
    assert found == ["lorem ipsum", "todo", "fixme"]  # MARKERS' own order, not text order


def test_clean_text_finds_nothing():
    assert find_leftover_markers("Q3 revenue grew 12% year over year.") == []


def test_empty_string_finds_nothing():
    assert find_leftover_markers("") == []


def test_every_marker_is_lowercase_already():
    # find_leftover_markers relies on comparing against a lowercased haystack;
    # a marker that isn't already lowercase would never match case-sensitively
    # written source text using the marker's own casing.
    for m in MARKERS:
        assert m == m.lower()
