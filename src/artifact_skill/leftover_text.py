"""Shared "leftover generation artifact" text markers (Issue #14).

A generated document that still contains "Click to add title," "Lorem
ipsum," "ここに入力してください," or a stray "TODO"/"FIXME" almost certainly
wasn't reviewed before being called done - the exact class of defect an
agent's own generation step is prone to leaving behind and the default
(empty) verification policy doesn't currently catch at all.

Deliberately a small, literal marker list (case-insensitive) rather than a
general heuristic/NLP classifier: a false positive here dents trust in
every other check this project makes, and a short, explicit, reviewable
list is easier to reason about than a model. Each adapter decides what
"the text" means for its own format (DOCX paragraph text, PPTX shape/
placeholder text, HTML body text, PDF/SVG extracted text, XLSX cell
values) and calls `find_leftover_markers()` against it.

A short bare token (todo, fixme, tktk) is matched at word boundaries, not
as a raw substring - an external review of an earlier version of this
list correctly caught that plain `m in lower` made "todo" match inside
"Todolist"/"todos"/a variable named `TodoItem`. A multi-word phrase
("click to add", the Japanese markers) is matched as a substring instead:
Python's `\\b` is defined against `\\w`, and Japanese script has no
inter-word whitespace, so requiring a word boundary on both sides of a
multi-character Japanese phrase would silently stop matching realistic
placeholder text like "ここに入力してください" (there is no boundary between
"力" and the "し" that follows it) - a multi-word English phrase is
already long/specific enough that a bare substring match doesn't
meaningfully risk a false positive either.
"""

from __future__ import annotations

import re

# (marker, match_at_word_boundaries) - kept as data, not scattered regex
# literals, so the list stays reviewable at a glance. `xxxx` covers 3+
# consecutive x's (xxx/xxxx/XXXX...) via a small regex rather than one
# fixed-length literal, since "xxx" (3) is at least as common a filler
# convention as "xxxx" (4) and the previous fixed string missed it.
MARKERS: tuple[tuple[str, bool], ...] = (
    ("lorem ipsum", False),
    ("loremipsum", False),
    ("lorem.ipsum", False),  # FIX_PROMPT P3-1: a dot-separated variant seen in the wild
    ("click to add", False),
    ("insert text here", False),
    ("placeholder text", False),
    ("[placeholder]", False),
    ("dummy text", False),  # FIX_PROMPT P3-1
    ("sample text", False),  # FIX_PROMPT P3-1
    # Deliberately NOT adding a bare "placeholder" (FIX_PROMPT P3-1 also
    # suggested this): unlike "todo"/"fixme"/"tktk", it's an ordinary
    # English word with everyday legitimate technical-writing uses (e.g.
    # "this field is a placeholder for the real config value") - even
    # with word-boundary matching, that's a real sentence a bare-word
    # marker would still misfire on, and this list's whole design
    # principle (see module docstring) is that a false positive here
    # costs trust in every other check this project makes. "placeholder
    # text"/"[placeholder]" above already cover the shapes that are
    # actually specific enough to be safe.
    ("todo", True),
    ("fixme", True),
    ("tktk", True),
    ("xxxx", True),  # regex below also matches xxx/xxxxx/...
    # Japanese generation-artifact/placeholder phrases.
    ("ここに入力", False),
    ("タイトルを入力してください", False),
    ("ダミーテキスト", False),
    ("サンプルテキスト", False),
    ("仮のテキスト", False),
    ("プレースホルダー", False),  # FIX_PROMPT P3-1
    ("ダミーデータ", False),  # FIX_PROMPT P3-1
    ("サンプル文章", False),  # FIX_PROMPT P3-1
)


def _pattern_for(marker: str, word_boundary: bool) -> re.Pattern[str]:
    if marker == "xxxx":
        body = r"x{3,}"
    else:
        body = re.escape(marker)
    if word_boundary:
        return re.compile(rf"\b{body}\b", re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (marker, _pattern_for(marker, word_boundary)) for marker, word_boundary in MARKERS
)


def find_leftover_markers(text: str) -> list[str]:
    """Return the subset of marker labels found in `text` (case-insensitive),
    in MARKERS' own order. Empty list means none found."""
    return [label for label, pattern in _PATTERNS if pattern.search(text)]
