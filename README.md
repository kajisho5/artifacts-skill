# artifacts-skill

[![tests](https://github.com/kajisho5/artifacts-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/kajisho5/artifacts-skill/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A local-first verification and evidence-generation engine for
AI-generated documents.**

Your agent can already generate a PDF. It cannot tell you whether that PDF
is actually correct — right page count, right page size, not encrypted,
no leftover placeholder text, text that's actually extractable, and a
rendering that looks right when you open it. `artifacts-skill` closes that
gap: **inspect → plan → execute → render → structural verify → visual
verify → receipt**, run locally, with no cloud account and no API key.

Verification is the point of this project, not an afterthought bolted onto
a bigger editing tool. The mutating-operation catalog (see the table
below) is intentionally small and still growing — this is not a
document-generation or full-editing tool, and isn't trying to be one. See
[**"What this is not"**](SKILL.md#what-this-is-not) in `SKILL.md` for what
this project deliberately doesn't do.

> **SPEC** (Self-Producing Execution Contract), coined by this project's
> author [kajisho5](https://github.com/kajisho5) for
> [`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill): derive a
> tool's schema from the one thing that actually has to be correct — its
> own parser — instead of hand-authoring a second copy beside the code.
> `artifacts-skill` doesn't fully reach that (its CLI subcommands take
> generic `--args`/`--policy` JSON blobs, not a per-operation parser SPEC
> could introspect) — it gets SPEC's actual goal, no schema drift, a
> different way. → [full explanation](#what-is-spec)

```
Input                     artifacts-skill                    Output
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
artifacts-skill doctor                     # what's available locally
artifacts-skill inspect report.pdf --json  # what is this file, really
artifacts-skill receipt report.pdf \
  --operation metadata_set --args '{"title":"Q3 Report"}'
```

The last command runs the full lifecycle and writes
`reports/receipt.json` plus `reports/rendered/*.png` — the evidence, not
just a claim. Once published, the same commands work as
`npx artifacts-skill ...` with no local install at all.

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
| CSV | ✅ | ✅ | ✅ (Playwright + Chromium) | — (inspect/render/verify only, by design) |
| Markdown | ✅ | ✅ | ✅ (`markdown-it-py` + Playwright + Chromium) | — (inspect/render/verify only, by design) |
| EPUB | ✅ | ✅ | not yet (honestly `NOT_IMPLEMENTED`, see `docs/adapters.md`) | `metadata_set` |

All formats from the original design brief's Tier 1 and Tier 2, plus CSV/
Markdown/EPUB added beyond it, are now implemented. A genuinely
unrecognized file fails loudly with `ARTIFACT_TYPE_UNSUPPORTED`; a
recognized-but-not-yet-built format would fail with
`ARTIFACT_ADAPTER_NOT_IMPLEMENTED` — never a silent no-op. See
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
- **Contract-first.** `artifacts-skill contract --json` is the single
  source of truth. The MCP server (`python -m artifact_skill.mcp.server`)
  builds its `tools/list` (including every `inputSchema`) directly from
  it at runtime. The CLI is hand-written, not generated from the same
  contract at runtime — but a mechanical test (not a convention) asserts
  every schema property has a matching CLI flag and vice versa, so the
  two can't silently drift out of sync even without shared codegen. This
  is this project's own answer to **SPEC** — see below.

### What is SPEC?

**SPEC** (Self-Producing Execution Contract) is a pattern coined by this
project's author, [kajisho5](https://github.com/kajisho5), for
[`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill) — a prior,
video-processing project by the same author, and the structural reference
this project's own contract/CLI/MCP design was built against (see
`docs/research.md` §4): a tool's `input_schema` — the part of its contract
that has to track its CLI flag-for-flag — is never hand-authored beside
the code. It's derived, at run time, from the one thing that actually has
to be correct for the CLI to work at all: the tool's own parser.
`ffmpeg-skill`'s 28 tools are each a standalone script with its own
`argparse` parser, so its `_capture_parser()` can import each script,
intercept its `parse_args()` call, and build `input_schema` straight from
the live parser object — no second, hand-kept-in-sync copy, structurally.

`artifacts-skill` doesn't reach the same place the same way, and says so
rather than claiming it does:

- **The MCP side is real SPEC.** `core/contract.py`'s `TOOLS` list is the
  one place every tool's schema is declared. `mcp/server.py` builds its
  `tools/list` — including every `inputSchema` — directly from `TOOLS` at
  runtime; there's no second copy anywhere, and
  `mcp/server.py::call_tool()` validates every incoming call against that
  same schema before dispatching.
- **The CLI side isn't, structurally, and can't cleanly be.** Where
  `ffmpeg-skill`'s tools are standalone scripts with typed, per-tool
  flags, `artifacts-skill`'s `execute`/`plan`/`verify` subcommands take a
  generic `--args`/`--policy` JSON blob whose actual shape depends on
  which operation you're running (`metadata_set` wants `{"title": ...}`,
  `fit_page_size` wants `{"width_pt": ..., "height_pt": ...}`) — there's
  no single `argparse` parser to introspect the way each of
  `ffmpeg-skill`'s scripts has. `cli/main.py`'s subcommands are
  hand-declared.
- **The goal — no silent drift — still holds, enforced a different way.**
  A mechanical test,
  `tests/contract/test_cli_mcp_consistency.py::test_cli_flags_match_input_schema_properties_in_both_directions`,
  asserts every `input_schema` property has a matching CLI flag and every
  CLI flag is either a schema property or on an explicit, small CLI-only
  allowlist (`--json`, `--dry-run`, ...). It runs on every CI run. This
  doesn't make drift *impossible* the way `ffmpeg-skill`'s runtime
  derivation does — it makes drift *caught*, immediately, by CI, rather
  than merely possible to catch by someone noticing.

See `core/contract.py`'s module docstring for the exact mechanism and the
same honest comparison this section summarizes.

## Install

```bash
git clone https://github.com/kajisho5/artifacts-skill
cd artifacts-skill
pip install -e ".[all]"   # every adapter (PDF/PPTX/DOCX/XLSX/Image/HTML/SVG/CSV/Markdown/EPUB)
artifacts-skill doctor
```

Only need a subset? Install just what you use instead — `pip install -e
".[pdf]"` for PDF alone, `.[html,svg]` for the Playwright-backed adapters,
etc. (see `pyproject.toml`'s `[project.optional-dependencies]` for the
full list). PPTX/DOCX/XLSX also need a `soffice`/`libreoffice` binary on
`PATH` for rendering; HTML/SVG/CSV/Markdown need `playwright install
chromium` once after installing their extra (Markdown also needs
`markdown-it-py`, pulled in by `.[markdown]`). EPUB needs nothing beyond
the standard library. Run `artifacts-skill doctor` to see exactly what's
available and what's still missing — never assume.

Or, once published: `npx artifacts-skill doctor` (the npm package is a thin
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

Exposes the same nine tools (`artifacts-skill.inspect`, `.plan`, `.execute`,
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
- [`docs/research.md`](docs/research.md) — prior art, why this doesn't
  duplicate Anthropic's own document skills, and this project's own
  structural reference, [`ffmpeg-skill`](https://github.com/kajisho5/ffmpeg-skill)
  (§4, same author — see [What is SPEC?](#what-is-spec)).
- [`docs/roadmap.md`](docs/roadmap.md) — phase plan and what shipped in
  each phase.
- [`docs/benchmark.md`](docs/benchmark.md) — the scored fixture benchmark:
  how many known-broken fixtures verification actually catches, computed
  and enforced by CI, not asserted.
- [`docs/performance.md`](docs/performance.md) — real wall-clock cost per
  adapter (`inspect`/`execute`/`render`), runnable and re-measurable, not
  a permanent claim.
- [`docs/adapters.md`](docs/adapters.md) — per-format adapter design
  notes, backends, and real bugs found while building each one.

## License

[MIT](LICENSE)
