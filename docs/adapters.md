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

## Planned, not implemented

Registered in `_PLANNED` with the phase each is targeted for (see
`docs/roadmap.md` for phase definitions):

| Type | Phase | Primary backend candidate |
|---|---|---|
| PPTX | 2 | `python-pptx` (structural) + LibreOffice headless (render) |
| DOCX | 3 | `python-docx` (structural) + LibreOffice headless (render) |
| XLSX | 3 | `openpyxl` (structural) + LibreOffice headless (render) |
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
