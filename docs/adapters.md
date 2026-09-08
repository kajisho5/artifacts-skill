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

## Implemented: PDF (`adapters/pdf/adapter.py`)

- **Backends**: `pypdf` (structural read/write/merge, BSD-3) + `pypdfium2`
  (rendering, Apache/BSD dual). See `docs/research.md` §5 for why not
  PyMuPDF.
- **Operations**: `metadata_set` (title/author/subject/keywords),
  `merge` (append one or more additional PDFs, in order), `fit_page_size`
  (scale every page's content and media box to an exact `width_pt`/
  `height_pt`, non-uniformly — see "Fix loop" below for why this exists).
- **Structural checks**: PDF readability, page count (+ optional exact/
  range requirement), page size consistency (+ optional exact requirement
  with tolerance), encryption, embedded JavaScript actions, extractable
  text ratio, arbitrary metadata field matching, and font embedding.
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
- **Operations**: `metadata_set` (title/author/subject/keywords).
- **Structural checks**: PPTX readability, slide count (+ optional exact/
  range requirement), broken media references (unreadable image blobs),
  leftover empty placeholders (heuristic, `WARN` by default — see
  limitations), text presence, arbitrary metadata field matching, and
  `chart_validity: UNKNOWN` when the deck contains a chart (`SKIPPED` when
  it doesn't) — chart *presence* is detected, internal chart data
  correctness is not.
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
- **Why `page_count` is always `UNKNOWN`, not computed or omitted**: DOCX's
  XML has no fixed page count — pagination is a function of the layout
  engine (fonts, margins, the actual rendering pass), something
  `python-docx` fundamentally cannot compute from the document part alone.
  Reporting a number derived from paragraph count would be a fabricated
  proxy; omitting the check would hide a real gap (spec §43, "Unknown is
  first-class"). A true page count *is* obtainable — after `render()`
  converts the document and produces real PDF pages — but that is a
  render-time, visual-adjacent fact, not something `verify_structural()`
  can determine on its own, and blurring that line is exactly what
  `docs/verification.md`'s structural/visual split exists to prevent.
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
  network-off-by-default policy), `formula_cached_errors`, and
  `formula_recalculation`.
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
  still work normally.
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

## Implemented: SVG (`adapters/svg/adapter.py`)

- **Backends**: structural inspection uses only the standard library
  (`xml.etree.ElementTree`) — `svg.structural` is always `AVAILABLE`, same
  design as HTML. Rendering reuses `rendering/chromium_render.py` — the
  exact module the HTML adapter's render logic was extracted into once a
  second adapter needed it, the same pattern as `office_convert.py` for
  PPTX/DOCX/XLSX.
- **No mutating operations** — same reasoning as HTML: SVG's natural edit
  is markup.
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

## Planned, not implemented

Every currently-known `ArtifactType` now has a real adapter — `_PLANNED`
is empty. See `docs/roadmap.md`'s "Later" section for what's out of scope
for the near term entirely (audio/video/3D/CAD, per the original design
brief's Tier 3 and beyond) rather than "planned but not started."

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
