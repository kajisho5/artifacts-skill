# Roadmap

Phases per the original design brief. **Phase 0-1, Phase 2 (PDF + PPTX),
Phase 3 (DOCX + XLSX), and a working slice of Phase 4/5 are done as of
this writing** — everything below "Now" is planned, not implemented, and
nothing in this codebase claims otherwise (`doctor`/`registry.py` report
unimplemented formats explicitly). All four Tier 1 formats from the
original design brief (PDF/PPTX/DOCX/XLSX) are now implemented.

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

## Now / Next

All four Tier 1 formats (PDF, PPTX, DOCX, XLSX) are implemented, and the
PDF font-embedding gap flagged since the MVP is closed. Nothing is
currently in progress — see "Later" below for what's next, and
`docs/adapters.md`'s "Writing a new adapter" checklist for how to start
one of them.

## Later

- **Phase 6 — HTML, SVG, Image.** Chromium (via Playwright, already
  available in CI-like dev environments) for HTML/SVG rendering; Pillow
  (already a dependency) for raster image normalization/preview. Network
  access for externally-referenced assets (remote fonts/images/stylesheets)
  stays off by default per `docs/security.md` — referenced-but-unfetched
  externals are reported, not silently fetched.
- **Phase 7 — Ecosystem integration, CI, benchmark, artifact corpus.**
  Expand `tests/fixtures/` into a scored benchmark per spec §54 (count of
  known-broken fixtures correctly detected per format); publish
  `contract --json` in a way an external orchestrator (spec §40's
  "AI-video-production-OS" framing) can consume without invoking this tool
  itself.

## Explicitly not planned (see spec §62)

Cloud storage, a SaaS dashboard, a custom LLM, a custom OCR/Office/PDF/
browser renderer built from scratch, automatic collection of material from
the internet, an unbounded auto-fix loop, and any subjective quality
judgment inside Core. If a future contributor is tempted to add one of
these, that's a sign the request belongs in a different project layered on
top of this one's receipt/contract output, not inside it.

## Fix-loop honesty note

The fix loop (`core/engine.py`, bounded by `Limits.max_fix_iterations`,
default 3) is architecturally generic today but has **zero registered
fixers** — `ArtifactAdapter.fix()` defaults to `None`, and the PDF adapter
doesn't override it. This is intentional: no PDF operation in the current
MVP has an obvious, safe automatic correction for a structural failure.
Adding a real fixer (e.g., for a future "fit content to page bounds"
operation) is Phase 2+ work and should come with its own fixture pair
(a deliberately-broken input and the expected fixed output) before it's
considered done — a fixer with no test proving it fixes something is a stub,
per spec §68.
