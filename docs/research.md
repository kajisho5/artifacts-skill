# Research — Market & Prior Art

Checked 2026-09-08. All star counts / repo states are point-in-time snapshots and will drift.
Where a claim could not be verified from a primary source, it is marked **UNKNOWN**.

## 1. Anthropic's official document Agent Skills

Repo: `github.com/anthropics/skills` — Anthropic's public Agent Skills repo. Contains
`skills/` (examples), `spec/` (Agent Skills spec), `template/` (scaffold), and document
skills at `skills/docx`, `skills/pdf`, `skills/pptx`, `skills/xlsx`.

- Most example skills are Apache-2.0; the four document skills are explicitly
  **source-available, not open source** — published "as a reference for more complex
  skills that are actively used in a production AI application," not guaranteed to match
  what Claude actually runs.
- `skills/pdf` contains `SKILL.md`, `LICENSE.txt`, `forms.md`, `reference.md`, `scripts/`.
  **UNKNOWN** — could not enumerate script contents to confirm whether they do structural
  verification, visual/render verification, receipts/provenance, or capability detection.
- `anthropics/skills#675` (open issue) confirms these document skills are considered "nearly
  invisible to users" — a discoverability problem, not a capability one.
- No evidence of bundled MCP support inside these skill folders. **UNKNOWN**.

**Implication:** Anthropic's skills are file-generation reference implementations, not
verification engines. Artifact Skill does not compete with them — it can *wrap* them (or
similar generators) and add the inspect/verify/receipt layer they don't claim to provide.

## 2. Agent Skills spec (SKILL.md)

- `platform.claude.com/docs` — Anthropic's own Agent Skills docs.
- `agentskills.io` — externally-maintained open standard ("originally developed by
  Anthropic, released as an open standard"), with a client list including Claude Code,
  Cursor, Codex, VS Code Copilot, Gemini CLI, OpenHands, Goose. GitHub org:
  `github.com/agentskills/agentskills`.

Spec requirements (`agentskills.io/specification`):
- Required frontmatter: `name` (≤64 chars, slug rules), `description` (1–1024 chars).
- Optional: `license`, `compatibility`, `metadata`, `allowed-tools` (experimental).
- Recommended dirs: `scripts/`, `references/`, `assets/`.
- Progressive disclosure: frontmatter (~100 tokens) loaded always; full body (<500 lines
  recommended) loaded on activation; resource files loaded on demand.
- Official validator: `skills-ref validate ./my-skill`.
- **The spec itself says nothing about structural/visual verification, receipts, or
  doctor commands** — these are conventions, not requirements. This is exactly the gap
  Artifact Skill fills.

## 3. Direct competitors

No project was found matching the full concept (structural + visual verification +
production receipt/provenance, across PDF/DOCX/PPTX/XLSX/HTML/SVG, as a local-first Agent
Skill). Adjacent, non-matching projects found via search:

| Name | Repo | What it does | Fit |
|---|---|---|---|
| Reverify | `2akouwu/reverify` | MCP+CLI; checks claims against ground truth, SHA-256 receipts | General claim verification, not document artifacts |
| ArtifactGuard | Glama MCP directory | Agents attest documents with on-chain (Hedera) receipts | Blockchain attestation, not render/structural QA |
| Provena | `rajfirke/provena` | Context governance / tamper-evident audit trail, EU AI Act | Compliance/governance tool, not document verification |

Stars/maintenance for these three: **UNKNOWN** — not confirmed from a direct repo fetch,
search-engine summaries only. None advertise local-first + no-API-key + dual
structural/visual verification for documents. This looks like a genuinely open niche, but
absence of evidence from a search-engine-only pass is not proof of absence.

## 4. kajisho5/ffmpeg-skill (prior project, same author — structural reference only)

Confirmed via GitHub page/README fetch (no API access to this repo in this session, so
figures come from rendered pages, not the API):

- ~28 Python scripts grouped by category (analysis, editing, audio, picture, delivery,
  orchestration) plus `_contract.py` and `mcp/server.py`.
- `doctor` — checks ffmpeg/ffprobe/component availability.
- `contract --json` — machine-readable tool spec; MCP server is derived from it
  (contract-first, no drift).
- Every tool supports `--dry-run`.
- MIT license.
- Stars: reported inconsistently as ~340–380 across two fetches — treat as approximate,
  not exact, as of 2026-09-08.
- Structural strengths worth mirroring (never copying content): typed args (argv arrays,
  no shell-string injection), verification runs by default rather than being opt-in,
  capability detection distinguishes MISSING from UNKNOWN, "no cloud / no API key / stdlib
  only" framing, and a documented test corpus. Exact test-count claims from a single
  fetched summary are **UNKNOWN** — not independently re-verified here.

## 5. Backend libraries (spot-verified)

| Tool | Capability | License | Local, no API key |
|---|---|---|---|
| LibreOffice headless | DOCX/PPTX/XLSX → PDF/image render | MPL-2.0 | Yes |
| Poppler (pdftoppm/pdftotext) | PDF render / text extract | GPLv2/v3 | Yes |
| qpdf | PDF structural transform/repair | Apache-2.0 | Yes |
| ImageMagick | Raster convert/compare | "ImageMagick License" (Apache-2.0-derivative, distinct SPDX id) | Yes |
| python-pptx / python-docx / openpyxl | Structural parse/edit | MIT | Yes |
| pypdf | PDF structural parse/edit/merge | BSD-3-Clause | Yes |
| pypdfium2 | PDF render (PDFium/Chromium engine bindings) | Apache-2.0 / BSD-3-Clause (dual) | Yes |
| PyMuPDF (fitz) | Fast PDF render/parse | **AGPL-3.0**, commercial license sold separately | Yes, but copyleft risk |
| pdfplumber | PDF structural/table extract | MIT | Yes |
| Playwright/Chromium | HTML/SVG render (screenshot) | Apache-2.0 | Yes (needs a browser binary, no API key) |

**Decision:** the PDF adapter uses **pypdf** (structural read/write/merge) +
**pypdfium2** (rendering) instead of PyMuPDF, specifically to avoid AGPL-3.0 obligations
for a tool meant to be embedded in other people's (possibly closed-source) agent
pipelines. This was confirmed in this environment: both packages install cleanly with pip
and have no native-binary prerequisite, which also serves the local-first requirement
better than shelling out to Poppler.

## 6. Visual-diff tools (brief)

- **pixelmatch** (`mapbox/pixelmatch`) — small, dependency-free pixel diff with
  anti-aliasing detection; the de facto lightweight screenshot-diff tool.
- **Resemble.js** — browser-based image diff, built for PhantomCSS-style visual
  regression.
- **SSIM** (structural similarity index) — commonly layered alongside pixelmatch to avoid
  false positives from anti-aliasing/font-rendering noise; not adopted for pixel-exact
  gating in this project (see `docs/verification.md`) because font/renderer drift across
  machines makes pixel-perfect comparison unreliable — semantic checks (overflow, overlap,
  blank pages, missing content) are prioritized instead, per this project's own design
  principle (spec §32).
- **Playwright `toHaveScreenshot()`** — built-in snapshot testing, same engine as the HTML
  rendering backend, so it can double as both renderer and comparator for the HTML/SVG
  adapters in a later phase.

## Conclusion

No existing tool combines: (a) a universal artifact abstraction across document formats,
(b) inspect-before-mutate + plan-before-mutate, (c) structural *and* visual verification
as first-class, separately-tracked results, (d) a machine-readable production receipt with
provenance, and (e) a contract that drives both a CLI and an MCP server with zero drift —
all local-first, with no account/API key. Anthropic's own document skills are generators,
not verifiers. The adjacent "receipt"/"attestation" tools found are either general-purpose
claim verification or blockchain attestation, not document structural/visual QA. This
supports building Artifact Skill as described in `docs/architecture.md`.
