---
name: artifact-skill
description: Local-first execution and verification engine for real-world artifacts (PDF, PPTX, DOCX, XLSX, PNG/JPEG/WebP, HTML, SVG). Use it whenever you create or modify a document-like file and need to prove it is actually correct before calling the job done — inspect what a file really is, plan a mutation before touching it, execute it without overwriting the original, render it to images, run structural checks, and get a machine-readable Production Receipt. Trigger on requests like "make sure this PDF is correct", "verify this document before I send it", "did the page count come out right", "check this file isn't corrupted", or any time you are about to say a generated artifact is "done" without having checked it.
license: MIT
compatibility: "Requires Python 3.10+. Install the `all` extra for every adapter, or a per-format extra (`pdf`, `pptx`, `docx`, `xlsx`, `image`, `html`, `svg`) for just what you need. PPTX/DOCX/XLSX rendering additionally needs a `soffice`/`libreoffice` binary on PATH; HTML/SVG rendering needs `playwright install chromium` after installing the `html`/`svg` extra. Run `artifact-skill doctor` to see real availability rather than assuming."
---

# Artifact Skill

You (the agent) generate PDFs, slide decks, spreadsheets, and other real
files. Generating a file is not the same as producing a correct one. This
skill gives you a CLI (`artifact-skill`) that inspects, mutates safely,
renders to images, and verifies — and it never just tells you "done"
without evidence.

**Currently implemented: PDF, PPTX, DOCX, XLSX** (all four Tier 1 formats),
**PNG/JPEG/WebP, HTML, and SVG.** Every format from the original design
brief's Tier 1 and Tier 2 is built. A genuinely unrecognized file returns
a clear `ARTIFACT_TYPE_UNSUPPORTED` error, never a silent no-op. HTML and
SVG have no mutating operations by design (inspect/render/verify only —
their natural "edit" is markup, i.e. source-code editing, not a
property-set operation this skill owns).

## What this is not

- **Not a document-generation tool.** This skill inspects, mutates a small
  set of safe properties (metadata, page selection/rotation, resize,
  format conversion), renders, and verifies — it does not create a PDF or
  slide deck from a prompt, template, or outline. Generate the file first
  (with whatever tool you already use for that), then bring it here.
- **Not OCR.** Text extraction here is limited to what's already
  extractable from the file's own structure (PDF text layer, DOCX/PPTX/
  XLSX XML). Scanned images with no text layer are out of scope.
- **Not a design or layout judge.** This skill renders pages to images and
  runs structural checks (page count, size, encryption, leftover
  placeholder text, and similar), but it never decides whether a layout
  "looks good," whether a color scheme works, or whether content is
  well-organized. That's a visual judgment call for you (the agent) or a
  human to make by actually looking at the rendered images — see the
  "Brain vs Hands" split in `docs/architecture.md`.
- **Not a cloud format-conversion service.** Everything runs locally
  against locally-installed backends (`pypdf`/`pypdfium2`, python-pptx/
  python-docx/openpyxl, LibreOffice, Playwright+Chromium, Pillow). No file
  is ever uploaded anywhere, and no functionality depends on a cloud
  account or API key.
- **Not decryption.** An encrypted PDF is detected and reported (`is_encrypted`
  in `inspect`, an `encryption` structural check) but there is no operation
  or argument anywhere in this skill to supply a password and decrypt —
  every command on an encrypted PDF stops there. If the task is "check
  this password-protected PDF," decrypt it with another tool first.

### Division of labor with Anthropic's own document skills

If Anthropic's own `docx`/`pdf`/`pptx`/`xlsx` skills
(`github.com/anthropics/skills`) are also installed in your environment,
they are the ones that *generate* those files from a prompt or template.
This skill does not compete with them — **generation is theirs,
verification is this skill's.** A typical flow: generate the document with
those skills (or any other generator), then run it through this skill's
inspect → execute → render → verify → receipt lifecycle before telling the
user the job is done. Don't route a "create a slide deck about X" request
here; do route a "make sure the deck you just created actually has 10
slides and no encryption" request here.

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
   "it's fine." **Read `checks[].status` by `id` for the specific things
   you care about, not just the top-level aggregate `status`.** The
   aggregate is worst-status-wins across every check the adapter ran
   (`docs/verification.md`) — a document that's genuinely fine on the
   dimension you actually care about can still show an aggregate
   `UNKNOWN` because an unrelated check (e.g. XLSX's
   `formula_recalculation`, always `UNKNOWN` whenever any formula is
   present, by design) couldn't be answered. That's `UNKNOWN` doing its
   job, not the preset being broken.
6. **Look at the rendered images yourself.** Rendering produces evidence;
   *you* are the one who judges whether the layout is actually right. This
   skill will never claim it visually verified something for you — that
   would be a fabricated judgment (see `docs/architecture.md` "Brain vs
   Hands").
7. **`receipt`** — or skip straight to this: it runs the whole
   inspect→execute→render→verify→[fix loop]→receipt lifecycle for one
   operation and writes `reports/receipt.json` (schema
   `artifact-receipt/v1`), which is what you should point to as evidence
   when you tell the user the job is done. The fix loop retries up to
   `Limits.max_fix_iterations` (3) by default — pass `--max-iterations 1`
   if you specifically want to disable it, not the other way around.

If a structural check FAILs, do not report success. Either fix the input
and re-run, or tell the user exactly what failed and why (the receipt's
`warnings`/`limitations` and each check's `message` field say this in
plain language already — surface them, don't paraphrase around them).

**Always pass `--policy` or `--policy-preset` when it matters.** The
default policy (`{}`) barely gates anything — a document with leftover
"Lorem ipsum"/"Click to add title" text, for example, only ever `WARN`s
(exit 0) under an empty policy, not `FAIL`s. Reach for a named preset
(`print-a4`, `print-letter`, `slides-16x9`, `spreadsheet-no-errors`,
`web-no-external` — see `policies.py`) before writing a policy dict by
hand; every preset already sets `forbid_placeholder_text: true`, so
leftover generation artifacts are a hard `FAIL` under any of them, not a
silent `WARN`. `execute`, `verify`, and `receipt` all accept
`--policy-preset`/`--policy` (explicit fields override the preset).

## Quick reference

The most common task is verifying a document you (or another tool) already
generated — most formats need no mutation at all, just a real policy:

```bash
artifact-skill doctor --json                                  # what's available on this machine
artifact-skill verify report.pdf --policy-preset print-a4      # a real submission gate, not the empty default
artifact-skill verify deck.pptx --policy-preset slides-16x9
artifact-skill verify page.html --policy-preset web-no-external
artifact-skill look report.pdf --out-dir reports/rendered      # then actually look at the rendered evidence
```

If the document also needs a mutation first (metadata, page selection,
etc.), chain through `execute`/`receipt` instead — `receipt` runs the
whole lifecycle (including the fix loop) in one call and still accepts
`--policy-preset`:

```bash
artifact-skill inspect report.pdf --json
artifact-skill plan report.pdf --operation metadata_set --args '{"title":"Q3 Report"}'
artifact-skill receipt report.pdf --operation metadata_set --args '{"title":"Q3 Report"}' \
  --policy-preset print-a4
artifact-skill render report_artifact_metadata_set.pdf --out-dir reports/rendered
artifact-skill look report.pdf --compare-to report_artifact_metadata_set.pdf   # before/after contact sheet

# Same lifecycle works on PPTX — same commands, same verbs:
artifact-skill receipt deck.pptx --operation metadata_set --args '{"title":"Q3 Deck"}' \
  --policy-preset slides-16x9 --policy '{"require_slide_count":10}'
```

Supported PDF operations today: `metadata_set` (title/author/subject/keywords),
`merge` (append additional PDFs), `fit_page_size`, `extract_pages`,
`delete_pages`, and `rotate_pages`. Supported PPTX operations: `metadata_set`
and `strip_placeholders`. Supported DOCX and XLSX operations today:
`metadata_set` (title/author/subject/keywords). Supported image (PNG/JPEG/
WebP) operations today: `resize` and `convert_format`. Run
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
- **No shell injection surface**: every subprocess call (the LibreOffice-
  backed PPTX/DOCX/XLSX renders, Playwright/Chromium for HTML/SVG) is an
  argv array against an explicit allowlist — never a shell string.

## When capabilities are missing

Run `artifact-skill doctor --json` first if a command fails with
`ARTIFACT_CAPABILITY_MISSING`. It tells you exactly what's
AVAILABLE/MISSING/UNKNOWN/NOT_REQUIRED/NOT_IMPLEMENTED and how to fix it
(usually `pip install -e ".[all]"` from the package root — a per-format
extra like `.[pdf]` only covers that one format's backends). Do not guess
at a workaround — the remediation field in the error is authoritative.

## MCP

The same nine commands are exposed as MCP tools (`artifact-skill.inspect`,
`artifact-skill.plan`, ...) via `python -m artifact_skill.mcp.server`
(stdio). Tool schemas there are generated from the same contract as this
CLI — see `docs/contract.md`.
