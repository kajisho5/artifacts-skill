# Roadmap

> **Status for a new contributor (2026-09-08):** all format adapters,
> the fix loop, MCP protocol negotiation, JSON Schema files, verification
> policy presets, an expanded mutation-operation catalog, and real
> LibreOffice/Chromium/macOS CI coverage are done — see GitHub issue #2
> (the roadmap tracker) for the authoritative, currently-open-vs-closed
> list, since this file reads chronologically (what happened, in what
> order) rather than as a live checklist. As of this writing the only
> items still open are Issue #9 (publish to PyPI/npm — deliberately not
> started without the repo owner's explicit go-ahead, since it's an
> external, irreversible action) and anything filed after this note.

Phases per the original design brief. **Phase 0-1, Phase 2 (PDF + PPTX),
Phase 3 (DOCX + XLSX), a working slice of Phase 4/5, and all of Phase 6
(Image + HTML + SVG) are done as of this writing** — everything below
"Now" is planned, not implemented, and nothing in this codebase claims
otherwise (`doctor`/`registry.py` report unimplemented formats
explicitly). All four Tier 1 formats from the original design brief
(PDF/PPTX/DOCX/XLSX) are now implemented, plus PNG/JPEG/WebP, HTML, and
SVG from Tier 2 — `adapters/registry.py`'s `_PLANNED` dict is now empty,
meaning every `ArtifactType` this project currently knows about has a
real adapter.

## Done

- **Phase 0 — Research.** `docs/research.md`.
- **Phase 1 — Core, CLI, contract, doctor, receipt, safe execution.**
  `core/`, `security/`, `cli/main.py`, `core/contract.py`,
  `doctor/detect.py`, `receipt/model.py`.
- **Phase 2 (PDF) + Phase 4 (verification/rendering framework, PDF only).**
  `adapters/pdf/adapter.py`: inspect, plan, execute (`metadata_set`,
  `merge`), render, structural verify. The visual "evidence vs. judgment"
  split from `docs/architecture.md` is implemented and format-agnostic
  already.
- **Phase 2 (PPTX).** `adapters/pptx/adapter.py`: inspect, plan, execute
  (`metadata_set`), LibreOffice-backed render (reusing the PDF adapter's
  page rasterizer via `rendering/pdf_pages.py`), structural verify. Proved
  the "evidence vs. judgment" split and the shared PDF-rendering helper
  both generalize cleanly to a second format with no Core changes — see
  `docs/adapters.md`. Also surfaced a real environment gotcha (a
  present-but-non-functional LibreOffice install) that shaped how
  `pptx.render` failures are now reported; see that doc for the detail.
- **Phase 5 (MCP) slice.** `mcp/server.py`, contract-derived `tools/list`,
  9 tools, tested for CLI/MCP schema parity. `core/contract.py`'s
  `artifact_types`/`capabilities` per tool are now computed from the
  adapter registry rather than hand-listed, so a new adapter appears in
  the contract automatically.
- **Phase 3 (DOCX).** `adapters/docx/adapter.py`: inspect, plan, execute
  (`metadata_set`), LibreOffice-backed render, structural verify. Reused
  the PPTX adapter's LibreOffice-conversion logic wholesale by extracting
  it into `rendering/office_convert.py` first (both adapters now call the
  same `convert_to_pdf()` rather than each having their own copy of the
  soffice-invocation code — see `docs/adapters.md`). Forced an honest
  design decision DOCX's format itself imposes: page count cannot be
  determined structurally at all (no rendering-independent pagination
  exists in the XML), so `verify_structural()` reports it as `UNKNOWN`
  unconditionally rather than faking a number from paragraph count.
- **Phase 3 (XLSX).** `adapters/xlsx/adapter.py`: inspect, plan, execute
  (`metadata_set`), LibreOffice-backed render, structural verify. Resolved
  the recalculation decision flagged in Issue #5: **not** attempting
  LibreOffice-macro-based recalculation (a materially larger attack
  surface for a speculative benefit), and instead reporting two separate
  honest facts — `formula_cached_errors` (checkable from whatever's
  already cached) and `formula_recalculation` (`UNKNOWN` whenever any
  formula exists, unconditionally, since no cached value proves current
  correctness). Verified against fixtures built by patching cached `<v>`
  values directly into the OOXML (since `openpyxl` itself can't write
  them and this dev sandbox's LibreOffice can't be relied on to produce
  them either) — see `docs/adapters.md` for the full design writeup and
  `tests/fixtures/generate_fixtures.py` for how those fixtures are made.
- **PDF font embedding (Issue #7).** `adapters/pdf/adapter.py`'s
  `font_embedding` check was, since the original MVP, unconditionally
  `UNKNOWN` — the canonical example this project used to explain "Unknown
  is first-class" (spec §43). It's now real: walks each page's
  `/Resources/Font` (through `Type0` composite fonts to the descendant's
  `/FontDescriptor`), checks for a `/FontFile`/`/FontFile2`/`/FontFile3`
  stream, and exempts the 14 standard PDF fonts. A plain, standard-font
  PDF now genuinely rolls up to overall `PASS`, not `UNKNOWN` — see
  `docs/verification.md` for the before/after and `docs/adapters.md` for
  the implementation detail.
- **Phase 6 (Image slice).** `adapters/image/adapter.py`: inspect, plan,
  execute (`resize`, `convert_format`), render, structural verify for
  PNG/JPEG/WebP — via Pillow alone, no external binary, the first adapter
  with no LibreOffice dependency at all. One adapter class covers all
  three formats (`adapters/registry.py::register_for_types()`), which
  exposed a real gap in `core/contract.py`'s dynamic capability-id
  computation (it would have produced three spurious per-subtype
  `image/png.*`/`image/jpeg.*`/`image/webp.*` ids instead of one shared
  `image.*`) — fixed by deriving capability ids from distinct adapter
  objects, not from `ArtifactType` values directly. Also added a genuine,
  format-specific honesty check: `exif_orientation` (`WARN` when a JPEG's
  EXIF orientation tag means its stored pixel grid isn't its display
  orientation) — `render()` corrects this in its evidence output, the raw
  file does not carry the correction.
- **Phase 6 (HTML slice).** `adapters/html/adapter.py`: inspect (stdlib
  `html.parser` only, no optional dependency), render (Playwright +
  Chromium), structural verify. No mutating operations — a deliberate
  scope decision, see `docs/adapters.md`. Hit the same "backend present on
  PATH/importable ≠ backend actually works" lesson PPTX's rollout
  surfaced for LibreOffice, this time for Chromium: this project's own dev
  sandbox had a pre-fetched Chromium build that didn't match the
  pip-installed `playwright` client's expected version, failing at launch
  until `playwright install chromium` fetched a matching one.
  `render()`'s error handling follows the same pattern established for
  LibreOffice (`ARTIFACT_RENDER_BACKEND_FAILED` with the real error
  attached, never a crash). Also the first adapter to *actively enforce*
  the network-off-by-default policy rather than only reporting on it — see
  `docs/security.md`'s Network policy section for how and why.
- **Phase 6 (SVG slice).** `adapters/svg/adapter.py`: inspect (stdlib
  `xml.etree.ElementTree` only, no optional dependency beyond the shared
  `playwright` render backend), render (reuses HTML's Playwright/Chromium
  pipeline via the newly-extracted `rendering/chromium_render.py`),
  structural verify. No mutating operations, same rationale as HTML. Two
  real findings, not just a port of HTML's adapter:
  - `Page.screenshot(full_page=True)` **hangs** (not errors — hangs until
    Playwright's own timeout) against a standalone SVG document; only
    discovered via a genuinely-hung test run. Fixed by adding a
    `full_page` parameter to the shared `render_local_file()` helper
    (`True` for HTML's existing behavior, `False` for SVG) — documented as
    a real limitation (SVG evidence images may be viewport-cropped) in
    `limitations()`, not silently worked around.
  - SVG is XML, and `xml.etree.ElementTree` is not hardened against
    entity-expansion DoS ("billion laughs") the way the existing
    `check_input_size` byte-cap alone does not prevent — a small file can
    expand to gigabytes in memory during parsing. Self-identified (not
    user-reported) and fixed with a new, tested security control,
    `_reject_xml_entities()`, applied before both `inspect()` and
    `render()` ever touch the file; see `docs/security.md`'s "XML entity
    expansion" section.

## Now / Next

All four Tier 1 formats (PDF, PPTX, DOCX, XLSX) are implemented, the PDF
font-embedding gap flagged since the MVP is closed, all of Phase 6
(Image, HTML, SVG) is done, and Issue #8's fix loop now has one real,
tested fixer (`pdf.fit_page_size`, see the "Fix-loop honesty note" above)
— see `docs/adapters.md` for the full per-adapter writeups. Remaining
candidates: Issue #9 (PyPI/npm distribution — requires explicit
confirmation before executing, since publishing is an external,
irreversible action). Issue #10 is resolved (see below) — the only
open item on the roadmap tracker is Issue #9.

## Later

Nothing currently — Issue #9 (PyPI/npm publishing) is the only item left
on the tracker, and it requires explicit user confirmation before
executing since publishing is an external, irreversible action.

## Phase 7 — Ecosystem integration + scored benchmark (Issue #10 — resolved)

**Scored benchmark.** `tests/benchmark/` (see `docs/benchmark.md` for the
full design writeup) is a declarative case table
(`tests/benchmark/cases.py`) plus a runner
(`tests/benchmark/run_benchmark.py`) scoring every deliberately-broken
fixture in `tests/fixtures/` against what `verify_structural()` (or, for
three formats, type detection itself) actually does with it — every
expected outcome was established by running the real adapter against the
real fixture and reading back the result, not predicted from source, per
spec §67. Enforced on every CI run as parametrized pytest cases
(`tests/benchmark/test_benchmark.py`), not just a number in a doc. Also
runs as a standalone script in CI for a human-readable report. Building
the case table surfaced a real shape this project hadn't explicitly
tracked before: PPTX/DOCX/XLSX's `corrupt.*` fixtures (and HTML's
`binary_garbage.html`) don't reach their own adapter's
`verify_structural()` at all — being zip-based OOXML, garbage bytes no
longer even sniff as that container type, so `ArtifactRef.from_path()`
returns `UNKNOWN` and `adapters/registry.py::get_adapter()` itself raises
`ARTIFACT_TYPE_UNSUPPORTED` one layer earlier. Still "correctly detected
as broken," just not via a `Check`.

**Ecosystem integration.** `examples/standalone_contract_consumer.py` is
the dogfood proof the issue asked for: a script that imports nothing from
`artifact_skill` and *discovers* which tool to call from
`artifacts-skill contract --json`'s own declared semantics
(`side_effects.mutates_input`/`writes_files`, `input_schema`) rather than
hardcoding a tool name — proving an external, unfamiliar orchestrator
(spec §40's "AI-video-production-OS" framing) really could drive this
tool from the contract alone. Exercised on every CI run via
`tests/integration/test_ecosystem_contract_consumer.py`, not just run
once by hand. The finer-grained `artifacts-skill.<format>.<capability>`
lookup convention `docs/contract.md` mentions was deliberately left
unbuilt — nothing outside this repo consumes it yet, and speculatively
building it now would be exactly the kind of premature abstraction
`docs/architecture.md` argues against.

**Cross-platform verification (spec §36).** At the time this section was
first written, this project's CI only ran `ubuntu-latest`, so "verified on
macOS/Windows" would have been a claim nothing actually checked — the
honest version of that work was a direct code audit against known
Windows/POSIX differences, documenting what was checked and what remained
genuinely unverifiable without a real macOS/Windows run:
- **Found and fixed a real bug**: `security/subprocess_exec.py`'s
  executable-allowlist check compared a resolved path's bare filename
  directly against adapters' platform-neutral allowlist names
  (`"soffice"`) — on Windows, `shutil.which()` returns a path ending in
  `soffice.exe`, which would never match and would have made every
  LibreOffice-backed render fail with `ARTIFACT_SUBPROCESS_NOT_ALLOWLISTED`
  on that platform. Fixed and covered by a test that's meaningful on this
  Linux CI too — see `docs/security.md`'s "Cross-platform allowlist
  matching."
- **Checked and confirmed already correct**: `security/paths.py`'s
  `resolve_within`/`atomic_write_bytes` (both built on `pathlib.Path` and
  `os.replace`, which are cross-platform by construction — `os.replace`
  is atomic on Windows too, via `MoveFileEx`, not just on POSIX);
  `security/paths.py::safe_extract_zip`'s symlink-member check (reads the
  zip's own stored Unix-style `external_attr` mode bits — a property of
  the archive format itself, not the host OS running this code, so it
  behaves identically regardless of platform); `doctor/detect.py` and
  `rendering/office_convert.py` (both `shutil.which`-based, which already
  handles `PATHEXT` resolution on Windows); the npm wrapper
  (`bin/artifacts-skill.js`, which falls back through `artifacts-skill` →
  `python3 -m` → `python -m`, using Node's own cross-platform
  `path.delimiter` for `PYTHONPATH`).
- **Not verified, and said so rather than assumed**: the npm wrapper
  doesn't try the Windows `py` launcher specifically, only `python3`/
  `python` — plausible on some Windows Python installs that don't
  register either name on `PATH`, but this is a documented gap, not a
  confirmed bug (unlike the subprocess allowlist issue above, nothing
  demonstrated this actually breaks anything).

**Closed the macOS half of this gap (Issue #13).** `.github/workflows/ci.yml`
now includes a real `macos-latest` run (LibreOffice via `brew install
--cask libreoffice`, Chromium via `playwright install chromium`) — CI
actually renders PPTX/DOCX/XLSX/HTML/SVG on macOS now, not just Linux.
A Windows runner would close the remaining gap but is still lower
priority than macOS was, per the reasoning above: no Windows-specific bug
has ever been found by the code audit, only fixed proactively.

## Explicitly not planned (see spec §62)

Cloud storage, a SaaS dashboard, a custom LLM, a custom OCR/Office/PDF/
browser renderer built from scratch, automatic collection of material from
the internet, an unbounded auto-fix loop, and any subjective quality
judgment inside Core. If a future contributor is tempted to add one of
these, that's a sign the request belongs in a different project layered on
top of this one's receipt/contract output, not inside it.

## Fix-loop honesty note (Issue #8 — resolved)

The fix loop (`core/engine.py`, bounded by `Limits.max_fix_iterations`,
default 3) was architecturally generic from the start but had, until now,
**zero registered fixers** — `ArtifactAdapter.fix()` defaults to `None`,
and no adapter overrode it. That was intentional at the time: no operation
in the MVP had an obvious, safe automatic correction for a structural
failure, and spec §68 forbids treating a stub as done.

`PdfAdapter` now has exactly one real fixer, proving the loop works
end-to-end rather than only in the abstract: a new `fit_page_size`
operation (`width_pt`/`height_pt` args, scales every page via pypdf's
`PageObject.scale_to()`) pairs with `PdfAdapter.fix()`, which handles
exactly one failure shape — `verify_structural()`'s existing
`page_size_requirement` check (driven by `policy["require_page_size_pt"]`)
failing because the operation was asked to scale to a size that doesn't
satisfy that policy (a realistic mistake: unit mix-ups, an approximate
target). The fixer reads the check's `expected_width_pt`/
`expected_height_pt` evidence and retries with the corrected size.
Anything else — a different operation, a different failed check, or
already being at the expected size and still failing — returns `None`
rather than guessing, per spec §16. `tests/unit/test_engine_lifecycle.py`
exercises the full retry path (iteration 1 fails, `fix()` adjusts args,
iteration 2 passes), and `tests/unit/test_pdf_adapter.py` unit-tests
`fix()`'s decision logic directly, including its "give up honestly"
branch.

Any future fixer for another adapter/operation should follow the same
shape: one well-defined failure mode with a deterministic, safe
correction, evidence-driven rather than guessed, with a test proving the
retry actually converges — not a speculative "smart" fixer that tries to
handle everything.
