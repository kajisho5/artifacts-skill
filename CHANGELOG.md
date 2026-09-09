# Changelog

All notable changes to this project are documented in this file, one
version section per release. Each entry here is also the source for the
release announcement text — when this file gets a new version section,
the announcement text is that section, verbatim or lightly trimmed.

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
