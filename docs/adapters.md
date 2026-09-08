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
  `merge` (append one or more additional PDFs, in order).
- **Structural checks**: PDF readability, page count (+ optional exact/
  range requirement), page size consistency (+ optional exact requirement
  with tolerance), encryption, embedded JavaScript actions, extractable
  text ratio, arbitrary metadata field matching, and an explicit
  `font_embedding: UNKNOWN` (not implemented — see `docs/verification.md`).
- **Render**: one PNG per page at 150 DPI via `pypdfium2`.
- **Known limitations** (also surfaced in every receipt via
  `adapter.limitations()`): font embedding completeness not checked;
  encrypted PDFs are detected but not decrypted automatically; JavaScript
  is detected, not analyzed or executed.

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

## Planned, not implemented

Registered in `_PLANNED` with the phase each is targeted for (see
`docs/roadmap.md` for phase definitions):

| Type | Phase | Primary backend candidate |
|---|---|---|
| HTML | 6 | Playwright/Chromium (already vendored in this dev environment) |
| SVG | 6 | Playwright/Chromium rasterization, or a pure-Python SVG rasterizer |
| Image (PNG/JPEG/WebP) | 6 | Pillow (already a PDF-adapter dependency) |

None of these are stubbed as "implemented." `doctor` reports the backend
libraries' import/PATH availability today (informationally, so a
contributor or user can see what to install ahead of time) even though no
adapter yet consumes them — this is deliberate: spec §68 forbids treating a
stub as done, but there's no harm in telling the truth about what's on the
machine early.

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
