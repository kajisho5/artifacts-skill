# artifact-skill

**Give your coding agent a production-grade artifact pipeline.**

Your agent can already generate a PDF. It cannot tell you whether that PDF
is actually correct — right page count, right page size, not encrypted,
no leftover placeholder text, text that's actually extractable, and a
rendering that looks right when you open it. `artifact-skill` closes that
gap: **inspect → plan → execute → render → structural verify → visual
verify → receipt**, run locally, with no cloud account and no API key.

```
Input                     artifact-skill                    Output
proposal.pdf   -->   inspect -> plan -> execute   -->   proposal_final.pdf
                          |                    |
                        render               verify
                          v                    v
                    reports/rendered/*.png   PASS / WARN / FAIL
                          \                   /
                           reports/receipt.json   (artifact-receipt/v1)
```

## Why

Agents fail silently on document artifacts constantly: text overflowing a
page, a PDF that opens to the wrong size, a merge that silently drops a
page, metadata nobody asked for. "I created the file" and "the file is
correct" are different claims. This project keeps them separate and makes
the second one provable — every run ends in a **PASS / WARN / FAIL /
UNKNOWN** verdict per check, backed by a machine-readable receipt, never a
guess.

## Quickstart

Not yet published to PyPI/npm (tracked in Issue #9) — for now, clone and
install locally (see **Install** below), then:

```bash
artifact-skill doctor                     # what's available locally
artifact-skill inspect report.pdf --json  # what is this file, really
artifact-skill receipt report.pdf \
  --operation metadata_set --args '{"title":"Q3 Report"}'
```

The last command runs the full lifecycle and writes
`reports/receipt.json` plus `reports/rendered/*.png` — the evidence, not
just a claim. Once published, the same commands work as
`npx artifact-skill ...` with no local install at all.

## What's implemented today

| Format | Inspect | Structural verify | Render (visual evidence) | Mutating operations |
|---|---|---|---|---|
| PDF | ✅ | ✅ | ✅ (`pypdfium2`) | `metadata_set`, `merge`, `fit_page_size`, `extract_pages`, `delete_pages`, `rotate_pages` |
| PPTX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set`, `strip_placeholders` |
| DOCX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set` |
| XLSX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set` |
| Image (PNG/JPEG/WebP) | ✅ | ✅ | ✅ (Pillow) | `resize`, `convert_format` |
| HTML | ✅ | ✅ | ✅ (Playwright + Chromium) | — (inspect/render/verify only, by design) |
| SVG | ✅ | ✅ | ✅ (Playwright + Chromium) | — (inspect/render/verify only, by design) |

All formats from the original design brief's Tier 1 and Tier 2 are now
implemented. A genuinely unrecognized file fails loudly with
`ARTIFACT_TYPE_UNSUPPORTED`; a recognized-but-not-yet-built format would
fail with `ARTIFACT_ADAPTER_NOT_IMPLEMENTED` — never a silent no-op. See
`docs/roadmap.md` for the phase plan and `docs/architecture.md` for why
the Core/Adapter split makes adding a format additive, not a rewrite.

## Design principles

- **Local-first.** No cloud, no account, no API key required. External
  network access is off by default everywhere.
- **Inspect before mutate, plan before execute.** `plan` never touches
  disk; `execute --dry-run` performs zero I/O and spawns zero subprocesses.
- **Original Protection.** The input file's hash is guaranteed unchanged —
  verified by the test suite, not just documented.
- **No hallucinated success.** Six explicit verification states — `PASS`,
  `WARN`, `FAIL`, `UNKNOWN`, `NOT_CHECKED`, `SKIPPED` — are never collapsed
  into each other. A missing capability reports `UNKNOWN`, not a quiet pass.
- **Brain/Hands split.** This engine renders and structurally checks; it
  never decides whether a design "looks good." Visual evidence is produced
  for the agent (or a human) to actually look at and judge.
- **Contract-first.** `artifact-skill contract --json` is the single
  source of truth. The CLI and the MCP server (`python -m
  artifact_skill.mcp.server`) are both generated from it — they cannot
  drift, and a test asserts they don't.

## Install

```bash
git clone https://github.com/kajisho5/artifacts-skill
cd artifacts-skill
pip install -e ".[all]"   # every adapter (PDF/PPTX/DOCX/XLSX/Image/HTML/SVG)
artifact-skill doctor
```

Only need a subset? Install just what you use instead — `pip install -e
".[pdf]"` for PDF alone, `.[html,svg]` for the Playwright-backed adapters,
etc. (see `pyproject.toml`'s `[project.optional-dependencies]` for the
full list). PPTX/DOCX/XLSX also need a `soffice`/`libreoffice` binary on
`PATH` for rendering; HTML/SVG need `playwright install chromium` once
after installing the `html`/`svg` extra. Run `artifact-skill doctor` to
see exactly what's available and what's still missing — never assume.

Or, once published: `npx artifact-skill doctor` (the npm package is a thin
wrapper that locates your Python 3 interpreter — the engine itself is
Python, see `docs/architecture.md` for why).

## Use from an agent

Point Claude Code, Cursor, Codex, or any Agent Skills–compatible agent at
`SKILL.md` in this repo. It documents the exact workflow above, the current
operations, and how to read a `PASS`/`WARN`/`FAIL` result.

## Use as MCP

```bash
python -m artifact_skill.mcp.server
```

Exposes the same nine tools (`artifact-skill.inspect`, `.plan`, `.execute`,
`.render`, `.verify`, `.look`, `.receipt`, `.doctor`, `.contract`) over
stdio, schema-identical to the CLI. Implements the legacy,
`initialize`-handshake-based MCP protocol (`2024-11-05` through
`2025-11-25`) — real clients from the newer, per-request-metadata protocol
revision (`2026-07-28`+) are specified as "dual-era" and fall back to this
handshake automatically, so this still interoperates; `initialize`
negotiates the client's requested version rather than ignoring it (see
`mcp/server.py`'s module docstring for the full compatibility note).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — Core/Adapter split, the
  lifecycle, why formats are additive.
- [`docs/contract.md`](docs/contract.md) — the machine-readable contract
  schema, in full.
- [`schemas/`](schemas/) — standalone JSON Schema files for the contract
  and receipt documents, validated against real generated output on every
  CI run (see `docs/contract.md`/`docs/verification.md`).
- [`docs/verification.md`](docs/verification.md) — the six-state
  verification model and how aggregation works.
- [`docs/security.md`](docs/security.md) — path/zip/subprocess safety
  model, resource limits, network policy.
- [`docs/research.md`](docs/research.md) — prior art and why this doesn't
  duplicate Anthropic's own document skills.
- [`docs/roadmap.md`](docs/roadmap.md) — phase plan and what shipped in
  each phase.
- [`docs/benchmark.md`](docs/benchmark.md) — the scored fixture benchmark:
  how many known-broken fixtures verification actually catches, computed
  and enforced by CI, not asserted.
- [`docs/adapters.md`](docs/adapters.md) — per-format adapter design
  notes, backends, and real bugs found while building each one.

## License

MIT
