"""The Artifact abstraction: what file is this, really?

Type detection never trusts the file extension alone (spec #7: "'.pptx'
だから正常なPPTX' と仮定しない"). It sniffs magic bytes, and for zip-based
Office Open XML containers it opens the archive and reads the content-type
declarations to tell PPTX/DOCX/XLSX apart from a generic .zip or a corrupt one.
"""

from __future__ import annotations

import csv
import enum
import hashlib
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024

# The Compound File Binary (CFB, aka "OLE2") container signature (fixed by
# the MS-CFB spec, MS-CFB §2.2 "Compound File Header" - the same 8 bytes for
# every CFB file ever written, not something specific to any one producer).
# A password-protected OOXML file (.pptx/.docx/.xlsx saved with encryption)
# is NOT a zip: Office wraps the whole encrypted zip package inside a CFB
# envelope (as an "EncryptedPackage" stream), so this project's zip-magic
# check never matches it at all. Legacy pre-2007 binary Office files
# (.doc/.ppt/.xls) use the same CFB container for their own, unrelated
# reasons. This project has no CFB/OLE2 parser (Issue #27) and isn't
# adding one just to say "this is encrypted" - recognizing the fixed
# signature is enough to give a specific, actionable error instead of a
# generic "unknown type" one.
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# EBML header (fixed by the Matroska/WebM spec) - the container format
# both use; ffprobe (adapters/media/adapter.py) tells them apart from the
# same file, so type detection only needs to recognize "this is EBML."
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"

# ISO Base Media File Format ("ftyp" box at byte offset 4) is shared by
# MP4/MOV/M4A/3GP *and* HEIC/AVIF still images (this project has no HEIC/
# AVIF adapter) and, in principle, any other ftyp-based format nobody has
# thought to name yet.
#
# Security-review finding: this used to be a *denylist* of known non-media
# brands (HEIC/AVIF) - fail-open, in the sense that any brand this project
# hadn't already thought to exclude got routed to the Media adapter's
# ffprobe/ffmpeg invocation by default, on the strength of nothing more
# than "it wasn't on the exclusion list." Given ffmpeg/ffprobe's own
# demuxer/decoder surface is large (and, per adapters/media/adapter.py's
# Limits.max_video_pixels docstring, was directly reproducibly abusable
# for a memory-exhaustion attack even through a *recognized* media brand),
# routing an unrecognized ftyp profile there by default was the less safe
# of the two designs. Flipped to an *allowlist* of known media major
# brands instead: an unrecognized brand now fails closed to UNKNOWN
# (an honest "don't know what this is," never a silent misdetection or a
# free pass into ffprobe/ffmpeg) rather than being fed to that surface by
# default. The cost is a real but modest one - an obscure, legitimate
# ftyp-based media format not in this list falls through to UNKNOWN
# instead of MEDIA - not a security problem, just a detection gap to
# widen this list for if it's ever actually hit in practice.
_FTYP_MEDIA_BRANDS = {
    b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash",
    b"M4A ", b"M4V ", b"M4P ", b"M4B ", b"qt  ",
    b"3gp1", b"3gp2", b"3gp3", b"3gp4", b"3gp5", b"3gp6", b"3g2a", b"3g2b",
}


class ArtifactType(str, enum.Enum):
    PDF = "pdf"
    PPTX = "pptx"
    DOCX = "docx"
    XLSX = "xlsx"
    HTML = "html"
    SVG = "svg"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_WEBP = "image/webp"
    ZIP = "zip"
    OLE_COMPOUND_FILE = "ole_compound_file"
    CSV = "csv"
    MARKDOWN = "markdown"
    EPUB = "epub"
    MEDIA = "media"
    UNKNOWN = "unknown"


# OOXML main-document content types, used to disambiguate a zip container.
_OOXML_CONTENT_TYPE_MARKERS: dict[str, ArtifactType] = {
    "presentationml.presentation": ArtifactType.PPTX,
    "wordprocessingml.document": ArtifactType.DOCX,
    "spreadsheetml.sheet": ArtifactType.XLSX,
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _sniff_zip_container(path: Path) -> ArtifactType:
    """Disambiguate a zip container: OOXML (PPTX/DOCX/XLSX, via
    `[Content_Types].xml`) or EPUB (via its mandatory `mimetype` member).

    EPUB's own spec requires `mimetype` to be the archive's first entry,
    stored (uncompressed) - real-world EPUBs occasionally violate that
    (checked separately as a WARN-level structural concern by the EPUB
    adapter, not here). For *type detection* only the member's presence and
    exact content are checked - a stricter "is it first/uncompressed" check
    here would misclassify an otherwise-genuine EPUB as ZIP/UNKNOWN, the
    same class of over-eager rejection spec #7 warns against.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                try:
                    mimetype = zf.read("mimetype").decode("ascii", errors="replace").strip()
                except (KeyError, OSError):
                    mimetype = ""
                if mimetype == "application/epub+zip":
                    return ArtifactType.EPUB
            try:
                content_types = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")
            except KeyError:
                return ArtifactType.ZIP
            for marker, atype in _OOXML_CONTENT_TYPE_MARKERS.items():
                if marker in content_types:
                    return atype
            return ArtifactType.ZIP
    except zipfile.BadZipFile:
        return ArtifactType.UNKNOWN


# --- CSV / Markdown: plain-text formats with no magic bytes at all --------
#
# Neither format has anything resembling a fixed signature - "sniffing" here
# necessarily means a heuristic over the decoded text, not a byte match like
# every other branch in detect_type(). Both lean toward the safe failure
# mode of this project's existing precedent (an unterminated leading HTML
# comment -> UNKNOWN, not a guess): a file that doesn't clear the bar stays
# UNKNOWN rather than being misclassified. Markdown is checked before CSV -
# a Markdown table (`| a | b |` rows) can otherwise look exactly like
# pipe-delimited CSV to a plain dialect sniffer.

_CSV_SNIFF_WINDOW = 65536
_CSV_SNIFF_MAX_LINES = 50
_CSV_ALLOWED_DELIMITERS = frozenset({",", "\t", ";", "|"})

_MD_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)", re.MULTILINE)
_MD_FENCED_CODE = re.compile(r"^ {0,3}(```|~~~)", re.MULTILINE)
_MD_LINK_OR_IMAGE = re.compile(r"!?\[[^\]\n]+\]\([^)\s]+\)")
_MD_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+\.)\s+\S", re.MULTILINE)
_MD_SETEXT_HEADING = re.compile(r"^\S.*\n {0,3}(=+|-+) *$", re.MULTILINE)
_MD_TABLE_SEPARATOR = re.compile(r"^ {0,3}\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^ {0,3}>\s?\S", re.MULTILINE)


def _decode_text_sample(head: bytes) -> str | None:
    """Strips a leading UTF-8 BOM after decoding (self-audit finding: a
    BOM - common from Excel/Windows editors/export tools - glued onto the
    first line broke the Markdown ATX-heading regex's `^#` anchor on that
    specific line, silently misdetecting an otherwise-obvious document as
    UNKNOWN; a CSV header would carry the BOM as part of its first cell
    name for the same reason)."""
    try:
        decoded = head.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:
            return None  # genuinely invalid, not just a boundary-cut multibyte char
        try:
            decoded = head[: exc.start].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    return decoded.removeprefix("\ufeff")


def _looks_like_markdown(text: str) -> bool:
    """Require at least two distinct CommonMark-ish signals, or one
    unambiguous one (a fenced code block or a table separator row) - a
    single ATX-heading-shaped line alone is indistinguishable from a shell
    ('# comment') or Python/YAML comment, so it can't count on its own.
    """
    strong = bool(_MD_FENCED_CODE.search(text)) or bool(_MD_TABLE_SEPARATOR.search(text))
    if strong:
        return True
    signals = 0
    for pattern in (_MD_ATX_HEADING, _MD_LINK_OR_IMAGE, _MD_LIST_ITEM, _MD_SETEXT_HEADING, _MD_BLOCKQUOTE):
        if pattern.search(text):
            signals += 1
            if signals >= 2:
                return True
    return False


def _sniff_csv_dialect(lines: list[str]) -> Any:
    """Try the Sniffer against the whole sample first, then fall back to
    just the first two (non-blank) lines - `csv.Sniffer` itself gives up
    ("Could not determine delimiter") on a sample containing even one
    ragged row, which would otherwise make a real, legitimately-CSV file
    with a single malformed row undetectable as CSV at all. A header plus
    one clean data row is almost always enough to identify the delimiter
    even when a later row is ragged.
    """
    for candidate_lines in (lines, lines[:2]):
        sample = "\n".join(candidate_lines)
        if not sample.strip():
            continue
        try:
            return csv.Sniffer().sniff(sample, delimiters="".join(_CSV_ALLOWED_DELIMITERS))
        except csv.Error:
            continue
    return None


def _looks_like_csv(text: str) -> bool:
    """`csv.Sniffer` plus a majority-consistency check across sampled rows
    - the Sniffer alone is too eager (it will confidently pick a
    "delimiter" out of ordinary prose containing commas); requiring most
    sampled rows to share the same, multi-column field count with an
    allowed delimiter is what turns "looks tabular-ish" into a real
    positive signal. Deliberately a *majority*, not *every* row: a real,
    legitimately-CSV file can still have the odd ragged row (that's a
    structural defect the adapter's own `column_count_consistency` check
    reports - it must not stop the file from being *detected* as CSV in
    the first place, or that check could never run at all).
    """
    lines = [ln for ln in text.splitlines()[:_CSV_SNIFF_MAX_LINES] if ln.strip()]
    if len(lines) < 2:
        return False
    dialect = _sniff_csv_dialect(lines)
    if dialect is None or dialect.delimiter not in _CSV_ALLOWED_DELIMITERS:
        return False
    rows = [r for r in csv.reader(lines, dialect) if r]
    if len(rows) < 2:
        return False
    field_counts = Counter(len(r) for r in rows)
    mode_count, mode_freq = field_counts.most_common(1)[0]
    if mode_count < 2:
        return False
    return mode_freq / len(rows) >= 0.6


def _strip_leading_markup_noise(data: bytes) -> bytes:
    """Strip leading BOM/whitespace and any leading HTML/XML comments
    (`<!-- ... -->`) before the real doctype/root tag (Issue #26).

    A license banner or generator comment (`<!-- Generated by ... -->`)
    before `<!DOCTYPE html>`/`<?xml ...?>`/`<svg ...>` is an extremely
    common real-world pattern this project's original bare `startswith()`
    prefix check couldn't see past at all — such a file sniffed as
    UNKNOWN even though it's unambiguously HTML/SVG to any real parser.
    Bounded to whatever's already in `data` (the same fixed-size head
    `detect_type()` reads): a comment whose closing `-->` falls outside
    that window is left as-is rather than guessing further, so a
    pathological case just reports UNKNOWN — the honest answer for
    something that doesn't look like anything recognizable within the
    sniffed window — rather than silently misdetecting it.
    """
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    while data.startswith(b"<!--"):
        end = data.find(b"-->")
        if end == -1:
            break
        data = data[end + 3 :].lstrip(b" \t\r\n")
    return data


def detect_type(path: Path) -> ArtifactType:
    """Detect artifact type from magic bytes (+ OOXML/EPUB content sniff,
    + a text heuristic for CSV/Markdown, which have no magic bytes at all)."""
    try:
        with open(path, "rb") as f:
            head = f.read(_CSV_SNIFF_WINDOW)
    except OSError:
        return ArtifactType.UNKNOWN

    if head.startswith(b"%PDF-"):
        return ArtifactType.PDF
    if head.startswith(_CFB_MAGIC):
        return ArtifactType.OLE_COMPOUND_FILE
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _sniff_zip_container(path)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ArtifactType.IMAGE_PNG
    if head[:3] == b"\xff\xd8\xff":
        return ArtifactType.IMAGE_JPEG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ArtifactType.IMAGE_WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ArtifactType.MEDIA
    if head[:4] == _EBML_MAGIC:
        return ArtifactType.MEDIA
    if head[4:8] == b"ftyp" and head[8:12] in _FTYP_MEDIA_BRANDS:
        return ArtifactType.MEDIA
    stripped = _strip_leading_markup_noise(head).lower()
    if stripped.startswith(b"<?xml") and b"<svg" in stripped[:2048]:
        return ArtifactType.SVG
    if stripped.startswith(b"<svg"):
        return ArtifactType.SVG
    if stripped.startswith((b"<!doctype html", b"<html")):
        return ArtifactType.HTML
    # FIX_PROMPT P2-3: a real, unremarkable XHTML document (an XML
    # declaration followed by an <html> root, e.g.
    # `<?xml version="1.0"?><html xmlns="...">`) fell through both the
    # SVG check above (no "<svg" anywhere in the window) and the bare
    # "<!doctype html"/"<html" check (the file starts with "<?xml", not
    # either of those) straight to UNKNOWN - confirmed by direct
    # reproduction before this fix. Deliberately narrow: only a
    # doctype-or-root-tag match still recognized as HTML, same as the
    # non-XML-declared case just above - a fragment with no <html> tag at
    # all is still honestly UNKNOWN (see SKILL.md).
    if stripped.startswith(b"<?xml") and b"<html" in stripped[:2048]:
        return ArtifactType.HTML
    text = _decode_text_sample(head)
    if text is not None:
        if _looks_like_markdown(text):
            return ArtifactType.MARKDOWN
        if _looks_like_csv(text):
            return ArtifactType.CSV
    return ArtifactType.UNKNOWN


@dataclass(frozen=True)
class ArtifactRef:
    """A concrete, on-disk artifact plus everything needed for provenance."""

    path: Path
    type: ArtifactType
    sha256: str
    size_bytes: int

    @classmethod
    def from_path(cls, path: Path | str) -> ArtifactRef:
        p = Path(path)
        if not p.is_file():
            from artifact_skill.core.errors import ArtifactInputError

            raise ArtifactInputError(
                code="ARTIFACT_INPUT_NOT_FOUND",
                message=f"Input file does not exist or is not a regular file: {p}",
                remediation="Check the path and try again.",
                evidence={"path": str(p)},
            )
        # Grok-review finding, verified by direct reproduction: this used
        # to call sha256_of(p) - a full streaming read of the entire file
        # - before any size check ran anywhere in the usual call chain
        # (from_path() is the near-universal first call for any
        # inspect/plan/execute/render/verify path). check_input_size() is
        # a cheap stat()-only check; running it first rejects an
        # oversized/malicious input immediately instead of paying the
        # full hashing cost first and only then discovering the file was
        # too large to accept.
        from artifact_skill.security.paths import check_input_size

        check_input_size(p)
        return cls(
            path=p,
            type=detect_type(p),
            sha256=sha256_of(p),
            size_bytes=p.stat().st_size,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "type": self.type.value,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class InspectionReport:
    """Format-agnostic envelope; adapters populate `details` with their own
    structured fields (page_count, slide_count, fonts, hyperlinks, ...)."""

    artifact: ArtifactRef
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "details": self.details,
            "warnings": self.warnings,
        }
