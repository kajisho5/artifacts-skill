"""Shared "leftover generation artifact" text markers (Issue #14).

A generated document that still contains "Click to add title," "Lorem
ipsum," or a stray "TODO"/"FIXME" almost certainly wasn't reviewed before
being called done — the exact class of defect an agent's own generation
step is prone to leaving behind and the default (empty) verification
policy doesn't currently catch at all.

Deliberately a small, literal substring list (case-insensitive) rather
than a general heuristic/NLP classifier: a false positive here dents
trust in every other check this project makes, and a short, explicit,
reviewable list is easier to reason about than a model. Each adapter
decides what "the text" means for its own format (DOCX paragraph text,
PPTX shape/placeholder text, HTML body text) and calls
`find_leftover_markers()` against it.
"""

from __future__ import annotations

MARKERS: tuple[str, ...] = (
    "lorem ipsum",
    "click to add",
    "insert text here",
    "placeholder text",
    "todo",
    "fixme",
    "xxxx",
)


def find_leftover_markers(text: str) -> list[str]:
    """Return the subset of MARKERS present in `text` (case-insensitive),
    in MARKERS' own order. Empty list means none found."""
    lower = text.lower()
    return [m for m in MARKERS if m in lower]
