# Adapters

## Interface

`src/artifact_skill/adapters/base.py:ArtifactAdapter` is the entire contract
between Core and a format. See `docs/architecture.md` for the method list.
Two rules matter most when writing a new adapter:

1. **`plan()` must be pure.** No writes, no subprocess calls. It exists so
   an agent (or a human) can see exactly what `execute()` would do before
   authorizing it — if `plan()` has side effects, that guarantee is gone.
2. **`execute()` writes only to the `output_path` it's given.** Never open
   the input path (`ref.path`) in write mode, ever, even if `output_path ==
   ref.path` — the CLI layer already refuses to default to that, but an
   adapter must not rely on the caller to enforce it.

## Registry (`adapters/registry.py`)

`register(AdapterClass)` maps `ArtifactType -> AdapterClass`. Types with no
registered adapter but a known future phase are listed in `_PLANNED`, so
`get_adapter()` raises a specific `ARTIFACT_ADAPTER_NOT_IMPLEMENTED` (with
the phase named) instead of a generic "unsupported type" — a caller and an
agent reading the error can tell "we haven't built this yet" apart from
"this isn't a real artifact type."

**`ArtifactType.OLE_COMPOUND_FILE` (Issue #27)** is a third case, distinct
from both of the above: `core/artifact.py::detect_type()` recognizes the
fixed CFB/OLE2 container signature (`D0 CF 11 E0 A1 B1 1A E1`) that a
password-protected Office 2007+ file (.pptx/.docx/.xlsx saved with
encryption isn't a zip at all — Office wraps the whole encrypted package
in a CFB envelope) and a legacy pre-2007 binary Office file (.doc/.ppt/.xls)
both use. There is no adapter and none is planned — this project has no
CFB/OLE2 parser and isn't adding one just to report "this is encrypted."
`get_adapter()` raises `ARTIFACT_OLE_COMPOUND_FILE_UNSUPPORTED` with a
remediation naming both likely causes, instead of collapsing this into the
same generic `ARTIFACT_TYPE_UNSUPPORTED` a file matching no known format
signature at all gets — a real .pptx that's simply password-protected
deserves a more specific answer than "unrecognized file."

## Implemented: PDF (`adapters/pdf/adapter.py`)

- **Backends**: `pypdf` (structural read/write/merge, BSD-3) + `pypdfium2`
  (rendering, Apache/BSD dual). See `docs/research.md` §5 for why not
  PyMuPDF.
- **Operations**: `metadata_set` (title/author/subject/keywords),
  `merge` (append one or more additional PDFs, in order), `fit_page_size`
  (scale every page's content and media box to an exact `width_pt`/
  `height_pt`, non-uniformly — see "Fix loop" below for why this exists),
  `extract_pages`/`delete_pages` (1-indexed page lists; `extract_pages`
  preserves the caller's order, so it can reorder or repeat pages too —
  both validate every page number against the real page count in `plan()`
  *and* `execute()`, not just execute(), via the shared
  `_resolve_page_indices()` helper), `rotate_pages` (a multiple of 90
  degrees, all pages by default or a specific 1-indexed subset).
- **Structural checks**: PDF readability, page count (+ optional exact/
  range requirement), page size consistency (+ optional exact requirement
  with tolerance), encryption, embedded JavaScript actions, extractable
  text ratio, blank pages (Issue #14 — neither extractable text nor an
  embedded image; known false positive: pure vector graphics), arbitrary
  metadata field matching, and font embedding.
- **Font embedding (Issue #7)**: walks each page's `/Resources/Font`
  (following `Type0` composite fonts to their descendant's
  `/FontDescriptor`) and checks for a `/FontFile`, `/FontFile2`, or
  `/FontFile3` stream. The 14 standard PDF fonts (Helvetica, Times,
  Courier, Symbol, ZapfDingbats and their bold/italic variants) are exempt
  — every conformant viewer must render them correctly unembedded, so
  their absence isn't a defect. Anything else unembedded is `WARN` by
  default, or `FAIL` under `policy.forbid_unembedded_fonts`. Before this
  was implemented, `font_embedding` was unconditionally `UNKNOWN`, which
  meant every real PDF's `verify` rolled up to `UNKNOWN` rather than
  `PASS` — see `docs/verification.md` for that history and why it was
  intentional at the time, not a bug.
- **Render**: one PNG per page at 150 DPI via `pypdfium2`.
- **Known limitations** (also surfaced in every receipt via
  `adapter.limitations()`): encrypted PDFs are detected but not decrypted
  automatically; JavaScript is detected, not analyzed or executed.
- **Fix loop (Issue #8)**: `PdfAdapter.fix()` is the one real, tested
  fixer in this codebase (every other adapter still returns the base
  class's `None`). It handles exactly one failure shape: `fit_page_size`
  was asked to scale to a size that doesn't satisfy a separately
  configured `policy["require_page_size_pt"]` (the `page_size_requirement`
  structural check). `verify_structural()` now attaches
  `expected_width_pt`/`expected_height_pt`/`actual_width_pt`/
  `actual_height_pt` to that check's `evidence` specifically so `fix()`
  can read the correct target back out of it rather than guessing;
  `fix()` retries with those exact values. Anything else — a different
  operation, a different failed check, or already targeting the expected
  size and still failing — returns `None`, per spec §16 ("no fixer" is
  the honest answer when there's nothing safe to try). See
  `docs/roadmap.md`'s "Fix-loop honesty note" for the fuller design
  writeup and `tests/unit/test_engine_lifecycle.py::test_fix_loop_actually_fixes_page_size_mismatch`
  for the end-to-end proof (iteration 1 fails, `fix()` corrects the args,
  iteration 2 passes).

## Implemented: PPTX (`adapters/pptx/adapter.py`)

- **Backends**: `python-pptx` (structural read/write, MIT, pure Python) +
  LibreOffice headless (`soffice --convert-to pdf`) for rendering, reusing
  the PDF adapter's `pypdfium2` page rasterizer via the shared
  `rendering/pdf_pages.py` helper rather than a second PNG-export
  implementation.
- **Operations**: `metadata_set` (title/author/subject/keywords),
  `strip_placeholders` (removes every placeholder shape left empty — the
  fixer counterpart to the `empty_placeholders` detection check below;
  python-pptx has no public shape-removal API, so this drops to
  `shape._element.getparent().remove(shape._element)` directly).
- **Structural checks**: PPTX readability, slide count (+ optional exact/
  range requirement), broken media references (unreadable image blobs),
  leftover empty placeholders (heuristic, `WARN` by default — see
  limitations), leftover generation-artifact text such as "lorem ipsum" or
  "click to add text" (`leftover_placeholder_text`, `WARN` by default,
  `FAIL` under `forbid_placeholder_text` — shared `leftover_text.py`
  marker list), optional slide aspect-ratio requirement
  (`require_slide_aspect_ratio` + `aspect_ratio_tolerance`), text presence,
  arbitrary metadata field matching, and `chart_validity: UNKNOWN` when the
  deck contains a chart (`SKIPPED` when it doesn't) — chart *presence* is
  detected, internal chart data correctness is not.
- **Render**: converts to PDF via LibreOffice headless through the shared
  `rendering/office_convert.py::convert_to_pdf()` (argv-only subprocess via
  `security/subprocess_exec.py`, allowlisted `{soffice, libreoffice}`,
  isolated per-call `UserInstallation` profile dir), then rasterizes with
  the same `rendering/pdf_pages.py` code path as the PDF adapter. The DOCX
  adapter (below) reuses this exact same conversion helper.
- **A real, load-bearing lesson from building this adapter**: `soffice`
  being found on `PATH` does not guarantee it can convert a given document
  — this project's own development sandbox has a LibreOffice install that
  launches successfully but fails to load *any* input file
  ("source file could not be loaded", exit 0, no output produced). Rather
  than let that surface as a crash or a silently-empty render,
  `PptxAdapter.render()` treats a missing output PDF as a hard failure and
  raises `ARTIFACT_RENDER_BACKEND_FAILED` carrying soffice's actual stdout/
  stderr in `evidence` — see the adapter module's docstring. `doctor` and
  `capabilities()` still report `pptx.render` as `AVAILABLE` when the
  binary is merely present on PATH (consistent with how `pdf.render`
  reports on `pypdfium2` importing, not on every possible PDF rendering
  correctly) — a present binary is a necessary, not sufficient, condition,
  and the render-time error is where the gap actually gets caught.
- **Known limitations** (surfaced via `adapter.limitations()`): chart
  internal validity not checked; empty-placeholder detection is a
  heuristic that can false-positive on intentionally blank section-header
  slides; embedded font completeness not checked; rendering depends on an
  external, sometimes-unreliable LibreOffice install.
- **If `strip_placeholders` starts silently doing nothing (or breaking) on
  a future python-pptx upgrade**: `shape._element.getparent().remove(shape._element)`
  reaches into python-pptx's internal `lxml` element tree because there is
  no public shape-removal API to call instead. This is a private-API risk
  by construction, not an oversight — if a python-pptx release changes how
  placeholder shapes are represented internally, the fix is to re-derive
  the removal call against that version's actual `_element`/`getparent()`
  shape (start from `tests/unit/test_pptx_adapter.py`'s
  `test_execute_strip_placeholders_removes_empty_ones` — a failure there
  is the signal), not to silently pin an old python-pptx version.

## Implemented: DOCX (`adapters/docx/adapter.py`)

- **Backends**: `python-docx` (structural read/write, MIT, pure Python) +
  the same `rendering/office_convert.py` LibreOffice-conversion helper the
  PPTX adapter uses.
- **Operations**: `metadata_set` (title/author/subject/keywords).
- **Structural checks**: DOCX readability, paragraph count (+ optional
  exact/range requirement — the closest pure-XML analogue to PDF's page
  count/PPTX's slide count that DOCX actually has), broken media
  references (unreadable embedded images), text presence, arbitrary
  metadata field matching, and an unconditional `page_count: UNKNOWN`.
- **Why `page_count` is `UNKNOWN` from `verify_structural()` alone**:
  DOCX's XML has no fixed page count — pagination is a function of the
  layout engine (fonts, margins, the actual rendering pass), something
  `python-docx` fundamentally cannot compute from the document part alone.
  Reporting a number derived from paragraph count would be a fabricated
  proxy; omitting the check would hide a real gap (spec §43, "Unknown is
  first-class"). `verify_structural()` itself never renders, keeping the
  structural/visual split `docs/verification.md` describes intact — a
  bare `verify` call still reports `UNKNOWN`, honestly.
- **`refine_structural_with_render()` (Issue #14)**: `execute`/`receipt`'s
  lifecycle already renders for visual evidence in the common case, and
  that render's page count *is* a real, measured fact (just not a
  format-intrinsic one). This adapter overrides the
  `ArtifactAdapter.refine_structural_with_render()` hook to swap the
  `UNKNOWN` `page_count` check for a `PASS` reporting the actual measured
  count — but only when a render with at least one page already happened
  as part of that same run (`core/engine.py::run_lifecycle()` calls this
  hook after both structural and visual results are known). This is
  upgrade-only: a bare `verify` call, or a run where rendering wasn't
  available/failed, is untouched and still reports `UNKNOWN`.
- **Render**: same `office -> PDF -> pypdfium2 page images` path as PPTX.
- **Known limitations**: page count not structurally determinable (see
  above); hyperlink validity not checked; numbering/list consistency not
  checked beyond basic package readability; rendering depends on an
  external, sometimes-unreliable LibreOffice install (same caveat as
  PPTX's render — this project's own dev sandbox reproduces it for DOCX
  too, confirming it's a backend/environment issue, not PPTX-specific).

## Implemented: XLSX (`adapters/xlsx/adapter.py`)

- **Backends**: `openpyxl` (structural read/write, MIT, pure Python) +
  the same `rendering/office_convert.py` LibreOffice-conversion helper.
- **Operations**: `metadata_set` (title/author/subject/keywords).
- **Structural checks**: XLSX readability, sheet count (+ optional exact/
  range requirement, optional required-sheet-names check), external
  workbook links (`WARN` by default — not fetched, per `docs/security.md`'s
  network-off-by-default policy), leftover generation-artifact text across
  every cell's string value (`leftover_placeholder_text`, `WARN` by
  default, `FAIL` under `forbid_placeholder_text` — shared
  `leftover_text.py` marker list), `formula_cached_errors`, and
  `formula_recalculation`.
- **XML entity-expansion guard (Issue #21)**: unlike python-pptx/
  python-docx, `openpyxl` only hardens its XML parsing when `lxml` or
  `defusedxml` happens to be importable — neither of which this project's
  own `xlsx` extra installs. `inspect()`/`execute()` both call
  `security/xml_safety.py::reject_xml_entities_in_zip()` before
  `openpyxl.load_workbook()` ever runs; see `docs/security.md` for the
  full audit (including why PPTX/DOCX did *not* need the same guard).
- **The recalculation decision (Issue #5)**: `openpyxl` cannot evaluate
  formulas — it can only read whatever cached result (if any) the last
  application to save the file computed. This adapter deliberately does
  **not** attempt LibreOffice-macro-based recalculation to work around
  that: a macro-scripting interface is a materially larger attack surface
  and failure-mode space than the plain `--convert-to pdf` conversion the
  render path already needs, for a benefit ("are formulas fresh") that's
  speculative pending an actual user request. Instead, two checks say
  plainly what is and isn't known:
  - `formula_cached_errors` — `SKIPPED` if there are no formulas at all;
    `PASS`/`FAIL` if cached results exist and are/aren't literal error
    tokens (`#REF!`, `#VALUE!`, ...); `UNKNOWN` if formulas exist but no
    cached values do (the common case for a workbook `openpyxl` itself
    wrote, confirmed against this adapter's own `good.xlsx` fixture).
  - `formula_recalculation` — `SKIPPED` if no formulas, otherwise
    `UNKNOWN` unconditionally, because no cached value (even a
    non-error one) proves the formula is *currently* correct relative to
    its inputs. This is why a workbook with real, non-error cached results
    still rolls up to overall `UNKNOWN`, not `PASS` — see
    `tests/unit/test_xlsx_adapter.py`'s test for this exact case.
- **Render**: same `office -> PDF -> pypdfium2 page images` path as
  PPTX/DOCX.
- **Known limitations**: formulas never recalculated (see above); chart
  and embedded-drawing validity not checked; conditional formatting and
  data validation rules not checked; rendering depends on an external,
  sometimes-unreliable LibreOffice install.

## Implemented: Image — PNG/JPEG/WebP (`adapters/image/adapter.py`)

- **Backend**: Pillow only (MIT, no external binary) — the first adapter
  with genuinely zero dependency on LibreOffice or any other subprocess,
  since Pillow reads and writes all three formats natively.
- **One adapter, three `ArtifactType`s**: `adapters/registry.py`'s
  `register_for_types()` maps `IMAGE_PNG`/`IMAGE_JPEG`/`IMAGE_WEBP` to the
  same `ImageAdapter` class, rather than three near-identical adapters.
  Capability ids are correspondingly `image.structural`/`image.render` —
  not `image/png.structural` etc. — see `core/contract.py`'s
  `_registered_adapter_ids()`, added specifically so the contract's
  dynamically-computed `capabilities` field wouldn't produce three
  spurious per-subtype ids that don't match what `capabilities()` actually
  reports (an inconsistency this project caught by testing against the
  actual adapter, not just eyeballing the diff).
- **Operations**: `resize` (pixel width/height, aspect-ratio-preserving by
  default via `Image.thumbnail`, or an exact stretch), `convert_format`
  (png/jpeg/webp; converting to JPEG composites any alpha channel onto
  white, since JPEG has no alpha).
- **Structural checks**: image readability (`Image.verify()`), dimensions
  (+ optional exact/range requirements), format requirement, and
  `exif_orientation`.
- **Why `exif_orientation` is a real check, not decoration**: a JPEG can
  carry an EXIF orientation tag telling viewers to rotate/flip the stored
  pixel grid before display. `Image.size` reports the *stored* grid, not
  the *displayed* one — so a `200x300` image with orientation 6 actually
  displays as `300x200`. This adapter reports the tag as `WARN` (not
  `FAIL` — the file isn't broken, just a trap for any downstream code that
  reads `width`/`height` and assumes it's the display size) and `render()`
  applies `ImageOps.exif_transpose()` so the evidence image it produces is
  the one a human/agent would actually see, not the raw grid.
- **Render**: normalizes to an upright PNG (see above) — the same "produce
  evidence" role render() plays for every other adapter, even though an
  image is already visual.
- **Known limitations**: animated images (APNG/animated WebP) are
  inspected by their first frame only; ICC color profiles are preserved on
  save where Pillow supports it but not validated; `convert_format`'s
  default output path keeps the input's extension (pass `--output`
  explicitly with the new one, or the file's content format and its
  filename extension will disagree — the content itself is always correct).

## Implemented: HTML (`adapters/html/adapter.py`)

- **Backends**: structural inspection uses only the standard library
  (`html.parser`) — no optional dependency, so `html.structural` is always
  `AVAILABLE` regardless of whether Playwright is installed. Rendering
  uses Playwright + Chromium.
- **No mutating operations** — deliberately. Every other adapter edits a
  well-defined property set (Office core metadata, image pixels); HTML's
  natural "edit" is changing markup, which is source-code editing, not a
  property-set operation this Skill should own. `operations()` returns
  `{}`, and `plan()`/`execute()` both raise a clear `ARTIFACT_OPERATION_
  UNKNOWN` for any operation name — `inspect`/`render`/`verify`/`look`
  still work normally, and so does `receipt` with no `--operation`
  (Issue #18's verify-only lifecycle path) — this is the one adapter type
  that path was specifically built for.
- **Structural checks**: readability (valid UTF-8 + parses), `<title>`
  presence (only checked when `policy.require_title` is set — a missing
  title isn't inherently wrong), local resource references that don't
  resolve to a file relative to the HTML file (`FAIL`), and external
  (`http`/`https`) resource references (`WARN` by default, `FAIL` under
  `policy.forbid_external_resources`).
- **Render enforces the network policy, not just reports it**: navigating
  a real page means Chromium will happily fetch every external resource it
  finds unless stopped. `render()` installs a Playwright route handler
  that aborts any request that isn't `file://`/`data:`/`about:` — so an
  external `<img>`/`<script>`/`<link>` renders visibly broken/missing
  rather than silently being fetched. This is the one adapter where
  `docs/security.md`'s subprocess-wrapper invariant needs an explicit,
  documented exception (Playwright manages its own browser process
  through its own API) — see that doc for the reasoning.
- **The same LibreOffice lesson applied to Chromium**: a `playwright`
  package being pip-installed doesn't guarantee a *matching* Chromium
  build is present — this project hit that directly (a pre-fetched
  Chromium build in its own dev sandbox didn't match the pip-installed
  `playwright` client's expected protocol version, and `playwright
  install chromium` was needed to fix it). `render()` treats any
  Playwright launch/navigation failure as `ARTIFACT_RENDER_BACKEND_FAILED`
  with the underlying error attached, never a crash; the render-happy-path
  test self-skips via a real probe, same pattern as the LibreOffice-backed
  adapters.
- **Known limitations**: JavaScript-driven content that renders
  asynchronously after `load` may not be captured; ARIA/accessibility
  structure is not inspected.
- **Type detection scope** (`core/artifact.py`'s `detect_type()`,
  FIX_PROMPT P2-3): recognizes a full HTML document — `<!doctype html>`,
  a bare `<html>` root, or an XHTML document (`<?xml ...?>` followed by
  `<html ...>`) — each after stripping any leading comment (Issue #26).
  A bare fragment with no `<html>` tag at all (e.g. starting directly with
  `<head>`/`<body>`, or a generation snippet with neither) is honestly
  `UNKNOWN`, not a guess — save such a fragment wrapped in a real
  `<html>` document if you want it recognized and verified as HTML.

## Implemented: SVG (`adapters/svg/adapter.py`)

- **Backends**: structural inspection uses only the standard library
  (`xml.etree.ElementTree`) — `svg.structural` is always `AVAILABLE`, same
  design as HTML. Rendering reuses `rendering/chromium_render.py` — the
  exact module the HTML adapter's render logic was extracted into once a
  second adapter needed it, the same pattern as `office_convert.py` for
  PPTX/DOCX/XLSX.
- **No mutating operations** — same reasoning as HTML: SVG's natural edit
  is markup. `receipt` with no `--operation` works here too, for the same
  reason (Issue #18).
- **A real security control HTML doesn't need**: SVG is XML, and
  `xml.etree.ElementTree` (an `expat`-based parser) is not hardened
  against entity-expansion ("billion laughs") DoS — a tiny file can
  decompress to gigabytes in memory before parsing completes, which the
  file-size cap in `security/limits.py` doesn't prevent on its own. Both
  `inspect()` and `render()` call `_reject_xml_entities()` first, which
  refuses to proceed at all if the file declares a `<!ENTITY` or a
  `<!DOCTYPE` with an internal subset — legitimate SVGs essentially never
  need one, so this is the same shape of defense as the zip-bomb
  compression-ratio check in `security/paths.py`: refuse outright rather
  than parse and hope the parser's own limits save it.
- **Structural checks**: well-formed XML with an `<svg>` root, explicit
  sizing (`width`/`height` or `viewBox` — `WARN` if neither is present,
  since a viewer then falls back to an arbitrary default size), local
  resource references (`image`/`use`/`script` `href`/`xlink:href`) that
  don't resolve to a file on disk (`FAIL`), and external resource
  references (`WARN` by default, `FAIL` under
  `policy.forbid_external_resources`) — deliberately not scanning `<style>`
  `@import`/`url()` references, see limitations.
- **A real rendering bug found and fixed while building this adapter**:
  `Page.screenshot(full_page=True)` hangs until timeout against a
  standalone SVG document (Chromium's synthetic top-level-document wrapper
  for a bare `<svg>` root doesn't behave like an HTML page for full-page
  sizing purposes) — confirmed directly, not assumed. `render_local_file()`
  in `rendering/chromium_render.py` now takes a `full_page` parameter;
  HTML keeps the default `True`, SVG passes `False`. A viewport-sized
  screenshot works reliably; a very large SVG's evidence image may be
  cropped to the viewport rather than showing all of its content (see
  `limitations()`).
- **Render**: same Chromium backend and active network-blocking as HTML.

## Implemented: CSV (`adapters/csv/adapter.py`)

- **Backend**: stdlib `csv` only — `csv.structural` is always `AVAILABLE`.
  `csv.render` needs Playwright (see below).
- **Type detection**: CSV has no magic bytes at all — `core/artifact.py`'s
  `_looks_like_csv()` is a genuine heuristic: `csv.Sniffer()` (retried
  against just the header + first row if the whole sample fails to sniff,
  since one ragged row is enough to make `Sniffer` give up entirely) plus
  a *majority*-consistency check across sampled rows (not "every row" —
  a real CSV can still have the odd ragged row, which is exactly what
  `column_count_consistency` below exists to catch; requiring 100%
  consistency at the type-detection layer would make that check
  unreachable for the file it should flag). A file that doesn't clear the
  bar is `UNKNOWN`, not guessed — same precedent as an unterminated
  leading HTML comment.
- **Operations**: none — a cell-value edit is source-data editing, the
  same "not a property-set operation this Skill should own" reasoning
  HTML/SVG already established.
- **Structural checks**: readability (valid UTF-8 + parses), row count
  (+ optional exact/range requirement), `column_count_consistency`
  (`FAIL` if any row's field count differs from the header's), and
  leftover generation-artifact text across every cell (shared
  `leftover_text.py` marker list).
- **Render**: builds a small, fully self-contained HTML `<table>` (every
  cell HTML-escaped, zero external/local resource references at all —
  nothing for the Chromium route handler to even need to block) and
  reuses `rendering/chromium_render.py`, the same backend HTML/SVG use.
  Capped at the first 500 rows, with the truncation surfaced in
  `RenderResult.warnings`; structural checks still cover the whole file.
- **Known limitations**: type detection is a heuristic, not a byte match
  (see above); encoding is assumed UTF-8.

## Implemented: Markdown (`adapters/markdown/adapter.py`)

- **Backends**: structural inspection is stdlib-only (regex over the
  decoded text) — `markdown.structural` is always `AVAILABLE`. Rendering
  needs the optional `markdown-it-py` (MIT, pure Python) plus Playwright;
  `markdown.render` degrades to `MISSING` without either, the same
  "losing one optional dependency doesn't collapse the whole format"
  precedent as every other adapter.
- **Type detection**: also heuristic (`core/artifact.py`'s
  `_looks_like_markdown()`), since plain text has no signature either.
  One strong signal (a fenced code block, or a table separator row —
  checked *before* CSV specifically so a Markdown table's `| a | b |`
  rows aren't mistaken for pipe-delimited CSV) or two weaker ones
  (heading/link-or-image/list-item/setext-heading/blockquote) are
  required — a single ATX-heading-shaped line alone is indistinguishable
  from a shell/Python/YAML `# comment` line, so it can't count alone.
- **Operations**: none — same "source-content editing, not a property-set
  operation" reasoning as CSV/HTML/SVG.
- **Structural checks**: readability, heading count (+ optional
  `require_heading`), `fenced_code_block_balance` (an odd fence-line count
  means an unclosed fence, which silently renders the rest of the document
  as code — a real correctness signal, not decoration), local link/image
  resolution, external link/image references (`WARN` by default, `FAIL`
  under `forbid_external_resources`), and leftover generation-artifact
  text — with fenced-code-block *bodies* excluded first, the same "code is
  not document text" reasoning as HTML's `<script>`/`<style>` exclusion (a
  "TODO" inside a demonstrated code sample isn't an unreviewed artifact).
- **Render**: `markdown-it-py` (CommonMark preset) converts to HTML,
  wrapped in a minimal page and fed through the same
  `rendering/chromium_render.py` backend CSV/HTML/SVG use — no new
  security surface, since the same network-blocking route handler applies.
- **Known limitations**: type detection and structural checks are both
  heuristic, not a full CommonMark parser (indented code blocks and
  reference-style links `[text][ref]` aren't specifically recognized);
  `render()` needs two optional dependencies, not one.

## Implemented: EPUB (`adapters/epub/adapter.py`)

- **Backend**: stdlib `zipfile` + `xml.etree.ElementTree` only —
  `epub.structural` is always `AVAILABLE`.
- **Type detection**: `core/artifact.py`'s zip-container sniff
  (`_sniff_zip_container()`, shared with the OOXML disambiguation PPTX/
  DOCX/XLSX use) checks for EPUB's mandatory `mimetype` member containing
  exactly `application/epub+zip` before falling back to the OOXML
  content-type check — real content, not the `.epub` extension.
- **`inspect()` never extracts to disk**: the adapter interface's
  `inspect()` contract says "must never write to disk" — every read here
  is `zipfile.ZipFile.read(name)` into memory, honored literally rather
  than worked around. `_guard_zip_bounds()` reimplements the
  decompression-bomb subset of `security/paths.py::safe_extract_zip()`'s
  checks (member count / total uncompressed size / per-member compression
  ratio, same limits, same error codes) without the extraction-only
  checks (path escape, symlink members), which don't apply to an
  in-memory read. `execute()` (the one place this adapter writes) doesn't
  extract either — it rewrites the archive in memory and writes the
  result once via `atomic_write_bytes()`.
- **XML entity-expansion guard, generalized from Issue #21**:
  `xml.etree.ElementTree` is `expat`-based and not hardened against
  entity-expansion DoS, the same exposure XLSX's own zip members had. The
  shared whole-zip pre-scanner (`reject_xml_entities_in_zip()`) filters by
  a bare `.xml` extension, which would silently skip EPUB's `.opf`/
  `.xhtml` members — rather than widen that already-tested scanner for a
  format it wasn't written for, this adapter calls the lower-level
  `reject_xml_entity_declaration()` directly on each specific member right
  before parsing it, the same one-parse-point shape SVG uses for its
  single file.
- **Operations**: `metadata_set` (title/author only — EPUB's Dublin Core
  metadata has no single-field analogue for `subject`/`keywords` the way
  Office metadata does).
- **Structural checks**: readability (container.xml → OPF parse
  succeeds), `mimetype_first_and_stored` (`WARN`, not `FAIL` — a real OCF
  requirement most real-world reading systems tolerate a violation of),
  spine count (+ optional exact/range requirement),
  `manifest_references_resolve` / `spine_references_resolve` (`FAIL` if a
  manifest item or spine itemref points at nothing real), optional
  `require_title` (dc:title presence), and leftover generation-artifact
  text scanned across each spine content document (capped at the first
  200, in spine order).
- **`verify_structural()` deliberately does not catch
  `ArtifactSecurityError`** (entity-bomb rejection, the zip-bomb guard) —
  matching the SVG/XLSX precedent for the same class of risk (see
  `tests/benchmark/cases.py`'s module docstring): a structural defect
  that is itself a security control propagates as an exception, not a
  mere `FAIL` `Check` a caller could route around.
- **Render: one PNG per spine (reading-order) document** (self-audit
  finding — this used to report `NOT_IMPLEMENTED`; closed by extending
  `rendering/chromium_render.py` rather than shipping a quick hack). The
  archive is safely extracted in full (`safe_extract_zip()` — the same
  path-escape/symlink/zip-bomb-guarded utility FIX_PROMPT P1-1 found
  declared but never actually wired into a production code path; this is
  that wiring) into a private temp directory, and every spine document is
  rendered from its staged copy with the *entire staged tree* as the
  Chromium `file://` allowed root, not just each document's own immediate
  directory — a chapter under `OEBPS/text/` pulling an image from a
  sibling `OEBPS/images/` is an entirely ordinary EPUB layout, and the
  narrower per-file boundary every other adapter uses would have
  incorrectly blocked it. The staged tree is nothing but this one EPUB's
  own content, so widening the boundary to all of it doesn't reopen the
  P0-2 hole (arbitrary local files) it exists to close — proven both ways
  directly against a real Chromium launch: a same-archive cross-directory
  image reference renders correctly, and a hostile `file:///etc/passwd`
  reference is still aborted. A document with more spine items than
  `Limits.max_pages` is rejected (`ARTIFACT_TOO_MANY_PAGES`), not silently
  truncated, the same as the PDF adapter.
- **Known limitations**: manifest/spine integrity is checked by presence,
  not by validating each content document's internal well-formedness
  beyond XML parsing; `metadata_set` supports title/author only; it also
  re-serializes the whole OPF via `xml.etree.ElementTree`, which silently
  drops any XML comments in it (ElementTree doesn't retain them by
  default) — every other archive member, XHTML content documents
  included, is copied byte-for-byte unchanged.
- **Self-audit finding, fixed before any external review saw it**:
  `metadata_set`'s OPF re-serialization was rewriting every element's
  namespace prefix from the original (virtually every real-world OPF
  declares its own namespace as the *default*, unprefixed one) to an
  auto-generated `ns0:` — `ET.tostring()`'s behavior for any namespace
  URI it has no registered prefix mapping for. Namespace-URI-aware XML
  parsers (this adapter's own `ET.fromstring()` included) don't care
  about the prefix name, but it was needless churn a strict validator or
  a human diffing the output shouldn't have had to see. Fixed by
  registering the OPF's actual namespace URI (read from the parsed
  root's own tag, not hardcoded) as the default prefix before
  serializing.

## Implemented: Media — video/audio (`adapters/media/adapter.py`)

- **Backend**: shells out to `ffprobe`/`ffmpeg` (`rendering/ffmpeg_probe.py`)
  — neither is a Python package this project can bundle, so
  `media.structural`/`media.render` report `AVAILABLE` only when the
  corresponding binary is found on `PATH`, the same present-binary-is-
  not-a-guarantee posture the PPTX/DOCX/XLSX adapters document for
  LibreOffice.
- **`ArtifactType.MEDIA` covers both audio and video** rather than
  splitting into separate types: type detection can only recognize the
  *container* from magic bytes (MP4/MOV/M4A-family via the ISO-BMFF
  `ftyp` box, WebM/Matroska via the EBML header, WAV via RIFF/WAVE) —
  telling audio-only from video apart needs an actual stream probe,
  which is exactly what `inspect()` does (`details["has_video"]`/
  `details["has_audio"]`). The `ftyp` check is an **allowlist of known
  media major brands**, not a denylist of known non-media ones
  (security-review finding — see below): an unrecognized brand (HEIC/
  AVIF still images also use `ftyp`, and so would any other ftyp-based
  format nobody has thought to name yet) fails closed to `UNKNOWN`
  rather than being routed to `ffprobe`/`ffmpeg` by default.
- **No mutating operations, by design, not by omission**: this project
  is a verification/evidence-generation engine, not a video editor (see
  "What this is (and isn't)" in `docs/architecture.md`). Producing or
  editing media is squarely another tool's job — this adapter's contract
  is the same one HTML/SVG/CSV/Markdown already have: inspect/render/
  verify, never execute.
- **Structural checks**: readability (`media_readable`, `FAIL` if
  `ffprobe` can't open the file — a truncated/corrupt file can still
  match a container's magic bytes at the type-detection layer),
  `has_stream` (`FAIL` if ffprobe opens the file but finds zero audio/
  video streams at all), `duration_known` (`UNKNOWN` if ffprobe reports
  no duration, `FAIL` if it's zero/negative, `PASS` otherwise), optional
  `min_duration_seconds`/`max_duration_seconds` range, optional
  `require_has_video`/`require_has_audio`, optional
  `require_min_resolution` (video only — `FAIL` with no video stream at
  all), optional `require_video_codec`/`require_audio_codec` (a string or
  a list of acceptable codec names), and leftover generation-artifact
  text scanned across `ffprobe`'s own container metadata tag *values*
  (title/comment/artist/etc. under `format.tags`) — not anything burned
  into the video frames themselves, which no OCR step here reads.
- **Render: one extracted frame, not a full playback check** — the same
  "give an agent something it can actually look at" contract every other
  adapter's `render()` honors, applied to video's natural analogue of "a
  page." Extracted at `min(2.0, duration/2)` seconds via
  `ffmpeg -ss <t> -frames:v 1`. An audio-only file (no video stream) has
  nothing to extract a frame from — `render()` returns zero files with a
  warning, not an error, the same "skip and say why" shape EPUB's
  `render()` uses for a spine item it can't render.
- **Known limitations**: MP3 (no reliable magic-byte signature without
  deeper frame parsing) and other containers beyond MP4/MOV/M4A-family,
  WebM/Matroska, and WAV are not recognized as `MEDIA`; corruption
  detection relies entirely on `ffprobe`'s own exit code and JSON output
  — a file `ffprobe` can open but that plays back incorrectly elsewhere
  is not detected; one extracted frame says nothing about motion, audio
  content, or frame-to-frame consistency.
- **Self-audit finding, fixed before any external review saw it**: both
  `office_convert.py::convert_to_pdf()` (pre-existing, affecting PPTX/
  DOCX/XLSX render()) and this adapter's own `ffmpeg_probe.py` passed a
  possibly-*relative* input path straight into the subprocess call —
  `security/subprocess_exec.py::run()` executes every call inside a
  fresh temporary working directory unrelated to the caller's own cwd,
  so a relative path (exactly what `ArtifactRef.from_path()` produces
  for a relative CLI argument, since it never resolves the path itself)
  silently failed to be found. Every existing test happened to use
  pytest's `tmp_path` fixture (always absolute), which is exactly why
  this went unnoticed until reproduced directly with a relative path.
  Fixed by resolving the path before use in both places, the same
  `.resolve()` `rendering/chromium_render.py::render_local_files()`
  already does for its own `source_path`.
- **Independent adversarial review, round 1 (correctness)**: a fresh-
  context review agent found two real bugs, both reproduced directly
  before being trusted. (1) `_first_stream()` matched an mjpeg
  attached-picture (cover art) stream as if it were real video — any
  audio file with embedded cover art (iTunes/Apple Music/podcast-tool
  M4A, a genuinely common case) got `has_video: True`, and `render()`
  then crashed trying to seek to a frame the attached-pic stream doesn't
  have. Fixed by excluding `disposition.attached_pic` streams. (2)
  `render()`'s frame-extraction seek time (`min(2.0, duration/2)`)
  assumed any nonzero seek point lands on a real frame — false for a
  genuine, non-corrupt short/single-frame-ish clip (confirmed with a
  real 0.01s H.264 MP4 whose only frame is at t=0). Fixed by retrying at
  t=0 on failure before giving up.
- **Independent adversarial review, round 2 (security)**: a second
  fresh-context pass found a real decompression-bomb-shaped resource-
  exhaustion issue, confirmed by direct reproduction: a container can
  declare an enormous *decoded* frame size while compressing to almost
  nothing on disk (a solid-color frame is trivially compressible) — a
  real 12000x12000 H.264 MP4 built this way is ~28KB on disk but made
  `ffprobe` alone peak at ~396MB RSS, and the adapter's full `render()`
  path (probe + frame decode) peak at ~1.4GB RSS over ~16s. `inspect()`
  now checks `width*height` against `Limits.max_video_pixels` (default
  ~64 megapixels, comfortably covering real 8K UHD content) immediately
  after `ffprobe` returns and before anything more expensive runs,
  raising `ArtifactSecurityError` — so `verify_structural()` inherits
  the guard too (and lets it propagate uncaught, the same security-
  control-not-a-mere-Check precedent SVG/XLSX/EPUB already established),
  not just `render()`'s own costlier decode step. The same review's
  fail-open `ftyp`-brand denylist finding is the allowlist change
  described above.
- **Independent adversarial review, round 3 (CLI/MCP surface, contract,
  docs)**: a third fresh-context pass, deliberately scoped away from the
  two axes above, actually drove the CLI end-to-end (`doctor`, `inspect`,
  `render`, `verify` with a Media policy, `receipt`, `contract --json`)
  against real ffmpeg-generated fixtures rather than reading code. Found
  one real bug: `render()`'s zero-file audio-only path returned before
  `out_dir.mkdir(...)`, so `--out-dir` was silently never created — unlike
  every other adapter's "skip and say why" render path (EPUB's included),
  which creates the directory unconditionally before deciding whether
  anything renderable exists. Fixed by moving the `mkdir` above the
  `has_video` check. The same pass also flagged a real but pre-existing,
  Media-independent issue: the generic "unknown policy key" error's
  remediation text pointed callers at `docs/verification.md` and `contract
  --json` for the list of valid per-format policy keys, but neither
  actually contains it (that list lives here, in this file). Fixed by
  pointing the remediation at `docs/adapters.md` instead
  (`core/engine.py`, `policies.py`). Everything else checked — `doctor`
  capability rows, `inspect`/`render` output on real fixtures including
  the round-1 cover-art fix and the round-2 resolution guard, policy
  array-to-tuple handling for `require_min_resolution`, the generated
  `contract --json` surface, MCP server dispatch (confirmed
  format-agnostic, no Media-specific branching), and every doc file's
  accuracy against current behavior — came back clean.
- **Independent adversarial review, round 4 (three parallel hostile
  passes — argument injection, malformed-container fuzzing, concurrency/
  policy edge cases)**: three fresh-context agents ran in parallel, each
  deliberately trying to break the adapter rather than just reading it,
  all against real ffmpeg-generated fixtures. Confirmed, fixed bugs:
  (1) `verify_structural()`'s raw unpacks/type coercions on
  `require_min_resolution`, `require_video_codec`/`require_audio_codec`,
  and `min_duration_seconds`/`max_duration_seconds` crashed with
  unhandled `ValueError`/`TypeError` on a malformed policy value (wrong-
  length list, wrong type, non-iterable) instead of a clean
  `ArtifactInputError` — including from the real CLI. Fixed with shape/
  type validation before use (the identical `require_page_size_pt` shape
  in the PDF adapter had the same bug; fixed there too). (2) `inspect()`
  crashed on ffprobe's real `"N/A"` sentinel for `sample_rate` — the
  adjacent `duration`/`bit_rate` fields already guarded this exact case,
  `sample_rate` was missed. (3) `render()`'s zero-file path mislabeled a
  genuinely stream-less (not just audio-only) file as "audio-only".
  (4) `render(limits=...)` accepted a custom `Limits` but called
  `self.inspect(ref)` with no way to forward it, so a caller-supplied
  `max_video_pixels` or subprocess timeout silently never reached the
  probe step — only `extract_frame()`'s own ffmpeg call honored custom
  limits. Fixed by giving `inspect()` its own optional `limits` parameter
  (additive to the base `ArtifactAdapter` signature) and having
  `render()` pass its `limits` through. (5) `require_video_codec`/
  `require_audio_codec` matched case-sensitively — ffprobe always reports
  codec names lowercase, so a reasonable-looking policy value like
  `"H264"` silently always FAILed; matching is now case-insensitive.
  (6) An over-long `--input` path (`ENAMETOOLONG`) made
  `ArtifactRef.from_path()`'s `is_file()` raise a bare `OSError` that
  escaped the CLI's `ArtifactError`-only top-level handler as a raw
  Python traceback instead of the tool's structured error contract — not
  Media-specific (every adapter's CLI path goes through `from_path()`),
  found and fixed via this adapter's own CLI surface. Also hardened as
  defense-in-depth (not a live bug — both call sites already `.resolve()`
  the path before building argv): added an explicit `--` before the
  positional path/output arguments in `ffmpeg_probe.py`'s ffprobe/ffmpeg
  invocations, so a future refactor that lost that ordering couldn't
  reopen an argument-confusion class of bug. Checked and came back
  clean: shell/command injection via filename, dash-prefixed filenames,
  symlinks (including a loop and links outside the working directory),
  `PATH`-based allowlist bypass, `check_input_size()` ordering (rejects
  an oversized file before ever invoking ffprobe), and NaN-policy
  injection (a `NaN` duration bound conservatively FAILs rather than
  silently bypassing the check). One finding was surfaced but
  deliberately **not** fixed in this pass: concurrent CLI/MCP calls that
  both default to the same `reports/` evidence directory can overwrite
  each other's rendered evidence file, so a receipt's `evidence` can
  point at a PNG a different, concurrent invocation actually wrote —
  reproduced with a 50% collision rate across 50 concurrent `receipt`
  calls. This isn't Media-specific (every adapter's `receipt`/`render`
  CLI/MCP path shares the same default), and fixing it means picking a
  new default-uniqueness scheme across the whole tool, not a Media-only
  change — flagged for a deliberate design decision rather than fixed
  as a drive-by (filed as Issue #47).

## Planned, not implemented

Every currently-known `ArtifactType` now has a real adapter — `_PLANNED`
is empty. See `docs/roadmap.md`'s "Later" section for what's still out of
scope entirely (CAD, 3D assets — considered and rejected when broadening
beyond Tier 1/2) rather than "planned but not started." Audio/video used
to be listed there too; see `docs/roadmap.md`'s Phase 9 for why that
call was revisited and the Media adapter above for what shipped.

`doctor` reports backend libraries' import/PATH availability
informationally (so a contributor or user can see what to install ahead
of time) even for adapters not yet consuming a given backend — this is
deliberate: spec §68 forbids treating a stub as done, but there's no harm
in telling the truth about what's on the machine early.

## Writing a new adapter — checklist

1. Add the `ArtifactType` (if new) to `core/artifact.py`, with a real
   content-sniff rule in `detect_type()` (never extension-only).
2. Implement `ArtifactAdapter` in `adapters/<format>/adapter.py`.
3. Every mutating operation gets an `OperationSpec` declaring its actual
   verification policy — don't mark `structural_verification_required=False`
   unless the operation genuinely can't be structurally checked.
4. `capabilities()` must probe for real (import checks, `shutil.which`),
   never assume.
5. Remove the type from `adapters/registry.py`'s `_PLANNED` map and call
   `register(YourAdapter)` in `_register_builtin_adapters()`.
6. Add fixtures under `tests/fixtures/<format>/` — at minimum one valid
   file and 2-3 deliberately broken ones (spec §31), and wire them into
   `tests/unit/test_<format>_adapter.py` plus
   `tests/contract/test_cli_mcp_consistency.py` picks the new tool schema
   up automatically once it's in `core/contract.py` — add a `ToolContract`
   entry only if the operation needs a new top-level CLI verb, not for
   every new operation (operations live inside `execute`/`plan`'s `--args`).
7. Update this file's "planned" table and `docs/roadmap.md`.
