# Roadmap

Phases per the original design brief. **Phase 0-1 and a working slice of
Phase 2/4 (PDF end-to-end) are done as of this writing** — everything below
"Now" is planned, not implemented, and nothing in this codebase claims
otherwise (`doctor`/`registry.py` report unimplemented formats explicitly).

## Done

- **Phase 0 — Research.** `docs/research.md`.
- **Phase 1 — Core, CLI, contract, doctor, receipt, safe execution.**
  `core/`, `security/`, `cli/main.py`, `core/contract.py`,
  `doctor/detect.py`, `receipt/model.py`.
- **Phase 2 (PDF slice) + Phase 4 (verification/rendering framework, PDF
  only).** `adapters/pdf/adapter.py`: inspect, plan, execute
  (`metadata_set`, `merge`), render, structural verify. The visual
  "evidence vs. judgment" split from `docs/architecture.md` is implemented
  and format-agnostic already — the next adapter gets it for free.
- **Phase 5 (MCP) slice.** `mcp/server.py`, contract-derived `tools/list`,
  9 tools, tested for CLI/MCP schema parity.

PPTX from Phase 2 is not yet started — see "Now" below.

## Now / Next

- **Phase 2 — PPTX.** `python-pptx` for structural inspect (slide count,
  placeholder text detection, missing media, broken relationships) +
  LibreOffice headless (already detected as `AVAILABLE` by `doctor` in this
  dev environment) for `pptx -> pdf -> page PNG` rendering, reusing the PDF
  adapter's render path where practical rather than reimplementing PNG
  export.
- **Phase 3 — DOCX, XLSX.** `python-docx` / `openpyxl` for structural
  inspect; LibreOffice headless for rendering. XLSX structural checks
  specifically need formula-error detection and recalculation-required
  flagging (spec §14) — `openpyxl` does not recalculate formulas itself,
  so this needs either a documented "not verified without LibreOffice
  recalculation" `UNKNOWN` state or a LibreOffice-macro-based recalculation
  path; decide before implementing rather than shipping a silent gap.

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
