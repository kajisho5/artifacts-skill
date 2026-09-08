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


def test_every_marker_label_is_lowercase_already():
    # find_leftover_markers matches case-insensitively regardless, but a
    # marker that isn't already lowercase in its own definition would be
    # confusing to read as "the" canonical spelling.
    for marker, _word_boundary in MARKERS:
        assert marker == marker.lower()


def test_short_token_does_not_false_positive_inside_a_longer_word():
    """Regression guard: an earlier version used a raw substring check
    (`m in lower`), so "todo" matched inside "Todolist"/"todos"/a variable
    named TodoItem. Bare short tokens are now matched at word boundaries."""
    assert find_leftover_markers("Add this to the Todolist app before Friday.") == []
    assert find_leftover_markers("All todos are tracked in the issue queue.") == []
    assert find_leftover_markers("class TodoItemFixture: pass") == []


def test_short_token_still_matches_as_its_own_word():
    assert find_leftover_markers("TODO: fill this in.") == ["todo"]
    assert find_leftover_markers("# FIXME - broken on Windows") == ["fixme"]


def test_xxxx_marker_matches_three_or_more_x_variants():
    """Regression guard: the previous marker was the literal string
    "xxxx" (exactly 4 x's), so a document filled with "xxx" (3, at least
    as common a filler convention) or "xxxxx" (5) was missed entirely."""
    assert find_leftover_markers("Client: XXX") == ["xxxx"]
    assert find_leftover_markers("Client: xxxxx") == ["xxxx"]
    assert find_leftover_markers("Client: xxxx") == ["xxxx"]


def test_finds_loremipsum_with_no_space():
    assert find_leftover_markers("loremipsum.io generated this block") == ["loremipsum"]


def test_finds_bracketed_placeholder():
    assert find_leftover_markers("Name: [placeholder]") == ["[placeholder]"]


def test_finds_japanese_placeholder_phrases():
    assert find_leftover_markers("ここに入力してください") == ["ここに入力"]
    assert find_leftover_markers("タイトルを入力してください。") == ["タイトルを入力してください"]
    assert find_leftover_markers("これはダミーテキストです。") == ["ダミーテキスト"]
    assert find_leftover_markers("サンプルテキストを挿入") == ["サンプルテキスト"]
    assert find_leftover_markers("仮のテキストです") == ["仮のテキスト"]


def test_clean_japanese_text_finds_nothing():
    assert find_leftover_markers("第3四半期の売上は前年比12%増加しました。") == []


def test_finds_p3_1_additional_markers():
    """FIX_PROMPT P3-1: real-world variants the original short list missed."""
    assert find_leftover_markers("This is dummy text for now.") == ["dummy text"]
    assert find_leftover_markers("Replace this sample text before publishing.") == ["sample text"]
    assert find_leftover_markers("lorem.ipsum dolor sit amet") == ["lorem.ipsum"]
    assert find_leftover_markers("ここに商品名のプレースホルダーがあります") == ["プレースホルダー"]
    assert find_leftover_markers("これはダミーデータです") == ["ダミーデータ"]
    assert find_leftover_markers("以下はサンプル文章です") == ["サンプル文章"]


def test_bare_placeholder_is_deliberately_not_a_marker():
    """FIX_PROMPT P3-1 also suggested a bare "placeholder" - deliberately
    not added (see leftover_text.py's comment): unlike todo/fixme, it's
    an ordinary word with legitimate everyday technical-writing uses."""
    assert find_leftover_markers("This field is a placeholder for the real config value.") == []
