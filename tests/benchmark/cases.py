"""The scored benchmark's case table (Issue #10, spec §54/§67).

Every expected value below was established by actually running each
adapter's `verify_structural()` (and, for the type-detection and
type-rejection cases, `ArtifactRef.from_path()`/`adapters.registry.get_adapter()`)
against the real fixture file and reading back what happened — not
predicted from reading the adapter source and not asserted on faith.
Spec §67 ("research integrity") applies to a detection-rate claim the
same way it applies to a research claim: don't state a number nothing
actually computed.

Two fixture shapes turned out to need different case categories, and the
difference itself is a real finding worth keeping visible rather than
flattening into one table:

- PDF, SVG, and PNG have distinct enough magic-byte/text signatures that
  a corrupted file (`corrupt.pdf`, `malformed.svg`, `corrupt.png`) still
  type-detects correctly and reaches its own adapter's
  `verify_structural()`, which reports a `<fmt>_validity` FAIL.
- PPTX/DOCX/XLSX are all zip-based OOXML; garbage bytes that used to be
  one of those no longer sniff as a valid zip at all, so
  `ArtifactRef.from_path()` returns `ArtifactType.UNKNOWN` and
  `adapters.registry.get_adapter()` itself raises
  `ARTIFACT_TYPE_UNSUPPORTED` — `verify_structural()` for that format is
  never even reached. Same for HTML's `binary_garbage.html`. This is
  still "correctly detected as broken", just one layer earlier than the
  zip-survives-but-content-doesn't-parse case.

`svg/entity_bomb.svg` is a third shape again: its structural defect *is*
a security control, so it raises `ArtifactSecurityError` out of
`verify_structural()` instead of returning a `Check` at all — the one
adapter whose `verify_structural()` doesn't catch that exception class
(only `ArtifactInputError`), which is deliberate (see `docs/security.md`).

`docx/good.docx` and `xlsx/good.xlsx` are known-good, valid files whose
overall status is genuinely `UNKNOWN`, not `PASS` — DOCX has no
structurally-determinable page count, and XLSX never recalculates cached
formula values, both by explicit design (`docs/verification.md`,
`docs/roadmap.md`). Scoring them as a "miss" would misrepresent an
intentional honesty feature as a detection failure, so they're listed
with their real expected status, not force-fit into PASS.

`mislabeled_pdf.<fmt>` (and the inverse `mislabeled_html.pdf`) prove
content-based type detection overrides a misleading extension — a
different capability than structural-defect detection, so they're scored
separately, not folded into the broken/good counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artifact_skill.core.artifact import ArtifactType
from artifact_skill.core.verification import CheckStatus

FIXTURES_ROOT = "tests/fixtures"


@dataclass(frozen=True)
class TypeRejectedCase:
    """Content no longer sniffs as any known type — get_adapter() itself
    must refuse before any format-specific verify_structural() runs."""

    fixture: str  # "<format_dir>/<filename>"
    description: str


@dataclass(frozen=True)
class StructuralDefectCase:
    """A real, reachable structural defect verify_structural() must flag."""

    fixture: str
    expected_status: CheckStatus
    description: str
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExceptionCase:
    """verify_structural() itself raises rather than returning a Check —
    the defect is caught by a security control, not a verification check."""

    fixture: str
    expected_exception_code: str
    description: str


@dataclass(frozen=True)
class KnownGoodCase:
    """A deliberately valid fixture — proves verification doesn't cry wolf.
    expected_status is PASS except where an honest UNKNOWN is by design
    (see module docstring)."""

    fixture: str
    expected_status: CheckStatus
    description: str
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TypeDetectionCase:
    """Content-based type detection must override a misleading extension."""

    fixture: str
    expected_type: ArtifactType
    description: str


TYPE_REJECTED_CASES: list[TypeRejectedCase] = [
    TypeRejectedCase("docx/corrupt.docx", "Garbage bytes no longer sniff as a valid zip/OOXML container."),
    TypeRejectedCase("pptx/corrupt.pptx", "Garbage bytes no longer sniff as a valid zip/OOXML container."),
    TypeRejectedCase("xlsx/corrupt.xlsx", "Garbage bytes no longer sniff as a valid zip/OOXML container."),
    TypeRejectedCase("html/binary_garbage.html", "Binary content doesn't sniff as HTML despite the extension."),
]

STRUCTURAL_DEFECT_CASES: list[StructuralDefectCase] = [
    StructuralDefectCase("pdf/corrupt.pdf", CheckStatus.FAIL, "Truncated/garbage PDF body; pdf_validity."),
    StructuralDefectCase("pdf/empty_0page.pdf", CheckStatus.FAIL, "Zero pages; page_count."),
    StructuralDefectCase(
        "pdf/blank_page.pdf", CheckStatus.WARN,
        "3 pages, the middle one has no extractable text and no embedded image; blank_pages (WARN by default) "
        "- a different failure mode from empty_0page.pdf (zero pages total).",
    ),
    StructuralDefectCase("pdf/encrypted.pdf", CheckStatus.WARN, "Encrypted; encryption (WARN by default)."),
    StructuralDefectCase(
        "pdf/nonembedded_custom_font.pdf", CheckStatus.WARN, "Non-standard font not embedded; font_embedding."
    ),
    StructuralDefectCase("pptx/empty_placeholder.pptx", CheckStatus.WARN, "Unfilled placeholder; empty_placeholders."),
    StructuralDefectCase("pptx/zero_slide.pptx", CheckStatus.FAIL, "Zero slides; slide_count."),
    StructuralDefectCase("docx/empty.docx", CheckStatus.FAIL, "No paragraphs; paragraph_count."),
    StructuralDefectCase("xlsx/formula_error.xlsx", CheckStatus.FAIL, "Cached #REF! error; formula_cached_errors."),
    StructuralDefectCase(
        "xlsx/external_link.xlsx", CheckStatus.UNKNOWN,
        "External workbook reference; external_links is WARN by default, but the formula "
        "referencing that external sheet has no cached value (openpyxl's writer never adds one "
        "for a formula it didn't compute), so formula_cached_errors/formula_recalculation are "
        "also UNKNOWN — which outranks WARN in aggregation (core/verification.py), making the "
        "overall status UNKNOWN, not WARN.",
    ),
    StructuralDefectCase(
        "docx/leftover_placeholder.docx", CheckStatus.UNKNOWN,
        "Contains 'Click to add title'/'Lorem ipsum'/'TODO'; leftover_placeholder_text is WARN by default, "
        "but page_count is always UNKNOWN for DOCX without a render (see docx/adapter.py), which outranks "
        "WARN in aggregation, making the overall status UNKNOWN, not WARN.",
    ),
    StructuralDefectCase(
        "pptx/leftover_placeholder_text.pptx", CheckStatus.WARN,
        "Filled-in placeholders containing 'Click to add title'/'Lorem ipsum'; leftover_placeholder_text "
        "(WARN by default) — a different case from empty_placeholder.pptx (placeholders left blank).",
    ),
    StructuralDefectCase(
        "pdf/leftover_placeholder.pdf", CheckStatus.WARN,
        "Contains 'Click to add title'/'Lorem ipsum'/'TODO' in extracted page text; leftover_placeholder_text "
        "(WARN by default) — unlike DOCX, PDF's page_count is never UNKNOWN, so this rolls up to WARN cleanly.",
    ),
    StructuralDefectCase(
        "xlsx/leftover_placeholder.xlsx", CheckStatus.WARN,
        "Contains 'Click to add title'/'TODO'/'Lorem ipsum' in cell text; leftover_placeholder_text (WARN by "
        "default) — no formulas present, so formula_cached_errors/formula_recalculation are SKIPPED (not "
        "UNKNOWN), unlike xlsx/external_link.xlsx below, so this rolls up to WARN cleanly.",
    ),
    StructuralDefectCase(
        "svg/leftover_placeholder.svg", CheckStatus.WARN,
        "Contains 'Click to add title'/'Lorem ipsum'/'TODO' across <text> elements; leftover_placeholder_text "
        "(WARN by default).",
    ),
    StructuralDefectCase("image/corrupt.png", CheckStatus.FAIL, "Truncated/garbage PNG; image_validity."),
    StructuralDefectCase("image/exif_rotated.jpg", CheckStatus.WARN, "EXIF orientation != 1; exif_orientation."),
    StructuralDefectCase(
        "html/missing_local_resource.html", CheckStatus.FAIL, "Local <img> target doesn't exist; local_resources."
    ),
    StructuralDefectCase(
        "html/leftover_placeholder.html", CheckStatus.WARN,
        "Contains 'Click to add title'/'Lorem ipsum' in body text (a 'TODO' inside <script> is correctly "
        "excluded - code, not document text); leftover_placeholder_text (WARN by default).",
    ),
    StructuralDefectCase(
        "html/external_resource.html", CheckStatus.WARN, "External resource reference; external_resources (WARN by default)."
    ),
    StructuralDefectCase(
        "html/no_title.html", CheckStatus.FAIL, "No <title>; title_presence.", policy={"require_title": True}
    ),
    StructuralDefectCase("svg/malformed.svg", CheckStatus.FAIL, "Malformed XML; svg_validity."),
    StructuralDefectCase(
        "svg/missing_local_resource.svg", CheckStatus.FAIL, "Local resource target doesn't exist; local_resources."
    ),
    StructuralDefectCase(
        "svg/external_resource.svg", CheckStatus.WARN, "External resource reference; external_resources (WARN by default)."
    ),
    StructuralDefectCase("svg/no_size.svg", CheckStatus.WARN, "No width/height/viewBox; explicit_size."),
    StructuralDefectCase("csv/ragged.csv", CheckStatus.FAIL, "One row has a different column count; column_count_consistency."),
    StructuralDefectCase(
        "csv/leftover_placeholder.csv", CheckStatus.WARN,
        "Contains 'TODO'/'Lorem ipsum' in cell text; leftover_placeholder_text (WARN by default).",
    ),
    StructuralDefectCase("markdown/unclosed_fence.md", CheckStatus.WARN, "Odd fence-line count; fenced_code_block_balance."),
    StructuralDefectCase(
        "markdown/missing_local_resource.md", CheckStatus.FAIL, "Local image target doesn't exist; local_resources."
    ),
    StructuralDefectCase(
        "markdown/external_resource.md", CheckStatus.WARN, "External link reference; external_resources (WARN by default)."
    ),
    StructuralDefectCase(
        "markdown/leftover_placeholder.md", CheckStatus.WARN,
        "Contains 'TODO'/'Lorem ipsum' in body text; leftover_placeholder_text (WARN by default).",
    ),
    StructuralDefectCase(
        "epub/broken_manifest.epub", CheckStatus.FAIL, "Manifest item references a missing archive member; manifest_references_resolve."
    ),
    StructuralDefectCase(
        "epub/broken_spine.epub", CheckStatus.FAIL, "Spine itemref references an unknown manifest id; spine_references_resolve."
    ),
    StructuralDefectCase(
        "epub/leftover_placeholder.epub", CheckStatus.WARN,
        "Contains 'TODO'/'Lorem ipsum' in chapter text; leftover_placeholder_text (WARN by default).",
    ),
    StructuralDefectCase(
        "epub/mimetype_not_first.epub", CheckStatus.WARN,
        "mimetype is present but not the archive's first entry, or not stored uncompressed; "
        "mimetype_first_and_stored (WARN by default — most real-world reading systems tolerate this).",
    ),
    StructuralDefectCase(
        "epub/missing_container.epub", CheckStatus.FAIL,
        "No META-INF/container.xml at all; epub_validity — caught as ArtifactInputError, not a security "
        "control, so (unlike entity_bomb.epub below) this is a Check, not an exception.",
    ),
    StructuralDefectCase(
        "media/leftover_placeholder.wav", CheckStatus.WARN,
        "Container metadata title tag contains 'TODO'; leftover_placeholder_text (WARN by default).",
    ),
    StructuralDefectCase(
        "media/corrupt_truncated.mp4", CheckStatus.FAIL,
        "A real MP4 truncated mid-stream; media_readable — ffprobe refuses it, caught as ArtifactInputError "
        "and reported as a Check, same shape as epub/missing_container.epub above.",
    ),
    StructuralDefectCase(
        "media/garbage_too_short.mp4", CheckStatus.FAIL,
        "Only the first 20 bytes of a real MP4 (still enough to match the 'ftyp' magic-byte check at the "
        "type-detection layer); media_readable — ffprobe has nowhere near enough data to read anything.",
    ),
]

EXCEPTION_CASES: list[ExceptionCase] = [
    ExceptionCase(
        "svg/entity_bomb.svg",
        "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED",
        "XML entity-expansion DoS payload; rejected before parsing, not reported as a Check.",
    ),
    ExceptionCase(
        "xlsx/entity_bomb.xlsx",
        "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED",
        "XML entity-expansion DoS payload in xl/worksheets/sheet1.xml (Issue #21) — rejected before "
        "openpyxl.load_workbook() is ever called, not reported as a Check.",
    ),
    ExceptionCase(
        "epub/entity_bomb.epub",
        "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED",
        "XML entity-expansion DoS payload in the OPF package document; rejected before ET.fromstring() "
        "is ever called, not reported as a Check.",
    ),
    ExceptionCase(
        "media/oversized_resolution.mp4",
        "ARTIFACT_MEDIA_RESOLUTION_TOO_LARGE",
        "9000x9000 declared frame size (a decompression-bomb shape — trivially compressible, ~16KB on "
        "disk) — rejected in inspect() right after ffprobe returns, before render()'s much more "
        "expensive frame decode ever runs, not reported as a Check.",
    ),
]

KNOWN_GOOD_CASES: list[KnownGoodCase] = [
    KnownGoodCase("pdf/good_2page.pdf", CheckStatus.PASS, "Clean 2-page PDF."),
    KnownGoodCase("pdf/embedded_font.pdf", CheckStatus.PASS, "Custom font, properly embedded."),
    KnownGoodCase("pptx/good_2slide.pptx", CheckStatus.PASS, "Clean 2-slide deck."),
    KnownGoodCase(
        "docx/good.docx", CheckStatus.UNKNOWN, "Valid document; page_count is honestly UNKNOWN by design (no fixed pagination in the XML)."
    ),
    KnownGoodCase(
        "xlsx/good.xlsx", CheckStatus.UNKNOWN,
        "Valid workbook with formulas; formula_recalculation is honestly UNKNOWN by design (openpyxl never recalculates).",
    ),
    KnownGoodCase("xlsx/no_formula.xlsx", CheckStatus.PASS, "Valid workbook with no formulas at all."),
    KnownGoodCase("image/good.png", CheckStatus.PASS, "Clean RGB PNG."),
    KnownGoodCase("image/alpha.png", CheckStatus.PASS, "Clean RGBA PNG."),
    KnownGoodCase("html/good.html", CheckStatus.PASS, "Clean HTML with a title.", policy={"require_title": True}),
    KnownGoodCase("html/no_title.html", CheckStatus.PASS, "No title, but title_presence isn't checked without the policy."),
    KnownGoodCase("svg/good.svg", CheckStatus.PASS, "Clean, self-contained SVG."),
    KnownGoodCase("csv/good.csv", CheckStatus.PASS, "Clean, consistent-column CSV."),
    KnownGoodCase("markdown/good.md", CheckStatus.PASS, "Clean Markdown with a heading, list, and fenced code block."),
    KnownGoodCase(
        "markdown/leftover_in_code_fence.md", CheckStatus.PASS,
        "A 'TODO' that appears only inside a fenced code block's body is correctly excluded from the "
        "leftover-text scan (code, not document text) — proves the exclusion works, not just that clean text passes.",
    ),
    KnownGoodCase("epub/good.epub", CheckStatus.PASS, "Clean EPUB: one spine chapter, valid manifest, mimetype first and stored."),
    KnownGoodCase("media/good.mp4", CheckStatus.PASS, "Clean MP4: H.264 video + AAC audio, both streams readable."),
    KnownGoodCase("media/good.webm", CheckStatus.PASS, "Clean WebM: VP9 video, no audio track."),
    KnownGoodCase("media/good.wav", CheckStatus.PASS, "Clean WAV: audio-only, no video stream at all."),
    KnownGoodCase(
        "media/audio_only.mp4", CheckStatus.PASS,
        "An MP4/M4A-family container that is genuinely audio-only — proves has_video/has_audio come from "
        "the real probed streams, not assumed from the container family.",
    ),
    KnownGoodCase(
        "media/audio_with_cover.m4a", CheckStatus.PASS,
        "Audio + an mjpeg attached-picture (cover art) stream — proves has_video correctly excludes "
        "disposition.attached_pic streams rather than treating cover art as real video content "
        "(adversarial-review finding, verified by direct reproduction).",
    ),
    KnownGoodCase(
        "media/single_frame.mp4", CheckStatus.PASS,
        "A 0.01s single-frame clip with a real frame only at t=0 — proves render() falls back to seeking "
        "t=0 when the computed midpoint has no frame, instead of raising (adversarial-review finding, "
        "verified by direct reproduction).",
    ),
]

TYPE_DETECTION_CASES: list[TypeDetectionCase] = [
    TypeDetectionCase("pdf/mislabeled_html.pdf", ArtifactType.HTML, "Real HTML content, misleading .pdf extension."),
    TypeDetectionCase("pptx/mislabeled_pdf.pptx", ArtifactType.PDF, "Real PDF content, misleading .pptx extension."),
    TypeDetectionCase("docx/mislabeled_pdf.docx", ArtifactType.PDF, "Real PDF content, misleading .docx extension."),
    TypeDetectionCase("xlsx/mislabeled_pdf.xlsx", ArtifactType.PDF, "Real PDF content, misleading .xlsx extension."),
    TypeDetectionCase("image/mislabeled_pdf.png", ArtifactType.PDF, "Real PDF content, misleading .png extension."),
    TypeDetectionCase("html/mislabeled_pdf.html", ArtifactType.PDF, "Real PDF content, misleading .html extension."),
    TypeDetectionCase("svg/mislabeled_pdf.svg", ArtifactType.PDF, "Real PDF content, misleading .svg extension."),
    TypeDetectionCase("csv/mislabeled_pdf.csv", ArtifactType.PDF, "Real PDF content, misleading .csv extension."),
    TypeDetectionCase("markdown/mislabeled_pdf.md", ArtifactType.PDF, "Real PDF content, misleading .md extension."),
    TypeDetectionCase("epub/mislabeled_pdf.epub", ArtifactType.PDF, "Real PDF content, misleading .epub extension."),
    TypeDetectionCase("media/mislabeled_pdf.mp4", ArtifactType.PDF, "Real PDF content, misleading .mp4 extension."),
]
