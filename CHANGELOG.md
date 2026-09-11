# Changelog

All notable changes to this project are documented in this file, one
version section per release. Each entry here is also the source for the
release announcement text — when this file gets a new version section,
the announcement text is that section, verbatim or lightly trimmed.

## v0.3.0

### Added
- automate version bumps from Conventional Commits PR titles (#56)

### Fixed
- require BREAKING CHANGE to be a footer trailer, not any substring (#57)

## v0.2.0

- New format: Media (video/audio), backed by ffmpeg/ffprobe — MP4/MOV/M4A,
  WebM/Matroska, WAV — following the same generation/verification split
  as every other adapter (structural facts from ffprobe, one extracted
  frame as visual evidence).
- PDF `merge`'s `plan()` now checks each `additional_inputs` entry is a
  readable, unencrypted PDF up front, instead of only discovering an
  unreadable or encrypted secondary file at `execute()` time.
- Consolidated the intra-artifact local-reference resolution logic shared
  by the HTML, SVG, and Markdown adapters into one helper
  (`reference_resolution.py`) — pure refactor, no behavior change; see
  `docs/architecture-evolution-review.md` for why a broader multi-artifact
  dependency graph was considered and deliberately not built.
- README restructured to front-load proof (real terminal-recording demos
  for every format) ahead of the pitch.
- Assorted hardening from independent adversarial review rounds (input
  validation, decompression-bomb and fail-open gaps in the Media adapter,
  concurrency).

## v0.1.0 — Initial release

`artifacts-skill` is a local-first verification and evidence-generation
engine for AI-generated documents: **inspect → plan → execute → render →
structural verify → visual verify → receipt**, run entirely on your own
machine, no cloud account and no API key.

- Formats: PDF, PPTX, DOCX, XLSX, PNG/JPEG/WebP, HTML, SVG, CSV, Markdown,
  EPUB — every Tier 1/2 format from the original design brief, plus
  CSV/Markdown/EPUB added beyond it.
- CLI (`artifacts-skill`) and MCP server, generated from one shared
  contract so they can't drift.
- Policy-driven structural verification (PASS/WARN/FAIL/UNKNOWN) plus
  rendered visual evidence for every mutating operation.
- Security model: sandboxed subprocess calls, path-escape and zip-bomb
  guards, XML entity-expansion protection, input size limits — all
  documented in `docs/security.md`.
- See `README.md` for the full pitch and `docs/roadmap.md` for what's
  next.
