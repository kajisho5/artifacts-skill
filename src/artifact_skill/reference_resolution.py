"""Shared local-reference resolution (Phase 0.5 consolidation).

Architecture Evolution Review (`docs/architecture-evolution-review.md`)
found that HTML, SVG, and Markdown each independently implement the exact
same algorithm for resolving an already-classified *local* reference
string against the referencing document's own directory: strip a trailing
`#fragment` (and, for HTML specifically, a trailing `?query` too), resolve
it, and report whether the result escapes that directory or exists as a
real file on disk. This module extracts exactly that shared step - nothing
more.

Deliberately NOT extracted, and NOT covered by this module (verified
against the review's own findings, re-checked directly against current
code before deciding):

- **What counts as "local" vs "external" vs some other, ignorable kind of
  reference** (a URL scheme classifier). This genuinely differs per
  format: Markdown treats `mailto:`/`data:`/a bare `#fragment` as one
  "other" bucket HTML has no equivalent for; SVG treats `data:`/a bare
  `#fragment` as "data" for its own reason (an SVG `<use href="#id">`
  referencing an internal `<defs>` element is a normal, common pattern,
  not a file reference at all); HTML has neither special case. Forcing
  one shared classifier would either lose a real distinction or invent a
  new one no adapter asked for - each adapter keeps its own classifier.
- **PPTX's/DOCX's `broken_media` check.** Reviewed directly before writing
  this module: the two adapters' *discovery* mechanism (`shape.image.blob`
  vs `document.part.related_parts` + a content-type filter) is entirely
  different, library-mediated code with nothing in common beyond both
  eventually producing a list of broken-reference identifiers. The only
  shared part left over - assembling that list into a `Check` - is four
  lines with adapter-specific message wording on either side; extracting
  it would trade a small amount of duplication for an indirection with no
  real safety or DRY benefit, so it was left alone.
- **EPUB's manifest/spine reference resolution.** Structurally different
  from the above: EPUB checks membership in a zip archive's member-name
  set (`href not in zf.namelist()`), never resolves a filesystem path, and
  has no "escapes the directory" concept to share with this module's
  filesystem-based logic. Forcing it into this shape would make EPUB's own
  manifest/spine semantics less clear, not more - left alone.
- **XLSX's `external_links`.** Presence-only (never resolved, since the
  target is a different workbook this project's network-off-by-default
  policy already forbids fetching) - there is no resolution algorithm
  here at all to extract.

See `docs/architecture-evolution-review.md` §32 (NARROW) for why this is
the full scope of Phase 0.5, and why nothing past this point (a graph, a
new policy key, a new CLI verb) is in scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class LocalReferenceOutcome(NamedTuple):
    """Result of resolving one already-classified local reference string.
    Callers own the exact Check id/message/evidence and warning text they
    build from this - this function has no adapter-specific wording baked
    into it, by design, so extracting it changes no observable behavior."""

    escaped: bool
    exists: bool


def resolve_local_reference(base_dir: Path, url: str, *, strip_query: bool = False) -> LocalReferenceOutcome:
    """Resolve a local reference string against `base_dir`, the same way
    the HTML, SVG, and Markdown adapters each did independently before
    this consolidation: strip a trailing `#fragment` (and, only when
    `strip_query=True`, a trailing `?query` too - HTML's own pre-existing
    behavior; SVG and Markdown never stripped a query string and still
    don't, since they never passed `strip_query=True`), resolve the
    result, and report whether it escapes `base_dir` or exists as a real
    file.

    Never raises for an escaping path - a hostile document is expected to
    try this, and the caller decides what to do about it (every existing
    caller warns and treats the reference as neither present nor missing,
    exactly as each adapter did before this function existed).
    """
    trimmed = url.split("#")[0]
    if strip_query:
        trimmed = trimmed.split("?")[0]
    candidate = (base_dir / trimmed).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        return LocalReferenceOutcome(escaped=True, exists=False)
    return LocalReferenceOutcome(escaped=False, exists=candidate.is_file())
