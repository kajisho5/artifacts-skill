---
name: artifact-skill
description: Local-first execution and verification engine for real-world artifacts (PDF now; DOCX/PPTX/XLSX/HTML/SVG planned). Use it whenever you create or modify a document-like file and need to prove it is actually correct before calling the job done — inspect what a file really is, plan a mutation before touching it, execute it without overwriting the original, render it to images, run structural checks, and get a machine-readable Production Receipt. Trigger on requests like "make sure this PDF is correct", "verify this document before I send it", "did the page count come out right", "check this file isn't corrupted", or any time you are about to say a generated artifact is "done" without having checked it.
license: MIT
compatibility: "Requires Python 3.10+. PDF support requires the `pdf` extra (pypdf, pypdfium2, Pillow)."
---

# Artifact Skill

You (the agent) generate PDFs, slide decks, spreadsheets, and other real
files. Generating a file is not the same as producing a correct one. This
skill gives you a CLI (`artifact-skill`) that inspects, mutates safely,
renders to images, and verifies — and it never just tells you "done"
without evidence.

**Currently implemented: PDF, PPTX, DOCX, and XLSX** — all four Tier 1
formats. HTML/SVG/Image are designed for (see `docs/architecture.md`,
`docs/roadmap.md`) but not yet built — running this skill against those
formats returns a clear `ARTIFACT_ADAPTER_NOT_IMPLEMENTED` error, never a
silent no-op.

## The workflow

Follow this order. Do not skip straight to "execute" on a file you have not
inspected, and do not report success without having verified.

1. **`inspect`** — read-only. Learn what the file actually is (page count,
   encryption, metadata, embedded JS, extractable text). Never trust a file
   extension; this command doesn't either — it sniffs content.
2. **`plan`** — pure, read-only. Before mutating anything, see exactly what
   `execute` would do: which adapter, what capabilities it needs, what files
   it will create (never the input file itself), and any risks.
3. **`execute`** — perform the operation. Writes only to a new output path
   (`<name>_artifact_<operation>.<ext>` by default). The input file is never
   overwritten, ever, even if you pass the same path as `--output`.
4. **`render`** — turn the result into page images (PNG) so you (the agent)
   can actually look at it, not just trust that bytes exist.
5. **`verify`** — run structural checks (page count, page size, encryption,
   JavaScript, extractable text, and anything else you pass as `--policy`).
   Reports one of six states per check — **PASS, WARN, FAIL, UNKNOWN,
   NOT_CHECKED, SKIPPED** — never collapses "we didn't check this" into
   "it's fine."
6. **Look at the rendered images yourself.** Rendering produces evidence;
   *you* are the one who judges whether the layout is actually right. This
   skill will never claim it visually verified something for you — that
   would be a fabricated judgment (see `docs/architecture.md` "Brain vs
   Hands").
7. **`receipt`** — or skip straight to this: it runs the whole
   inspect→execute→render→verify lifecycle for one operation and writes
   `reports/receipt.json` (schema `artifact-receipt/v1`), which is what you
   should point to as evidence when you tell the user the job is done.

If a structural check FAILs, do not report success. Either fix the input
and re-run, or tell the user exactly what failed and why (the receipt's
`warnings`/`limitations` and each check's `message` field say this in
plain language already — surface them, don't paraphrase around them).

## Quick reference

```bash
artifact-skill doctor --json                    # what's available on this machine
artifact-skill inspect report.pdf --json
artifact-skill plan report.pdf --operation metadata_set --args '{"title":"Q3 Report"}'
artifact-skill execute report.pdf --operation metadata_set --args '{"title":"Q3 Report"}'
artifact-skill render report_artifact_metadata_set.pdf --out-dir reports/rendered
artifact-skill verify report_artifact_metadata_set.pdf --policy '{"min_pages":1,"require_no_encryption":true}'
artifact-skill look report.pdf --compare-to report_artifact_metadata_set.pdf   # before/after contact sheet
artifact-skill receipt report.pdf --operation metadata_set --args '{"title":"Q3 Report"}'

# Same lifecycle works on PPTX — same commands, same verbs:
artifact-skill receipt deck.pptx --operation metadata_set --args '{"title":"Q3 Deck"}' \
  --policy '{"require_slide_count":10,"max_empty_placeholders":0}'
```

Supported PDF operations today: `metadata_set` (title/author/subject/keywords)
and `merge` (append additional PDFs). Supported PPTX, DOCX, and XLSX
operations today: `metadata_set` (title/author/subject/keywords). Run
`artifact-skill contract --json` for the exact, current, machine-readable
schema of every command — treat it as the source of truth over this prose
if they ever disagree.

## Rules this skill enforces even if you forget to ask

- **Original Protection**: the input file's hash never changes, period.
  `execute --dry-run` performs zero I/O and spawns zero subprocesses —
  verified by this project's own test suite.
- **No hallucinated success**: `PASS` is only returned when a check
  actually ran and actually passed. A capability that isn't installed
  reports `UNKNOWN`/`MISSING`, never a silent pass.
- **No network access** by default. This skill does not fetch external
  URLs, fonts, or images referenced inside a document.
- **No shell injection surface**: every subprocess call (used by future
  LibreOffice/Chromium-backed adapters) is an argv array against an
  explicit allowlist — never a shell string.

## When capabilities are missing

Run `artifact-skill doctor --json` first if a command fails with
`ARTIFACT_CAPABILITY_MISSING`. It tells you exactly what's
AVAILABLE/MISSING/UNKNOWN/NOT_REQUIRED/NOT_IMPLEMENTED and how to fix it
(usually `pip install -e ".[pdf]"` from the package root). Do not guess at
a workaround — the remediation field in the error is authoritative.

## MCP

The same nine commands are exposed as MCP tools (`artifact-skill.inspect`,
`artifact-skill.plan`, ...) via `python -m artifact_skill.mcp.server`
(stdio). Tool schemas there are generated from the same contract as this
CLI — see `docs/contract.md`.
