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

## 30 seconds

```bash
npx artifact-skill doctor                     # what's available locally
npx artifact-skill inspect report.pdf --json  # what is this file, really
npx artifact-skill receipt report.pdf \
  --operation metadata_set --args '{"title":"Q3 Report"}'
```

The last command runs the full lifecycle and writes
`reports/receipt.json` plus `reports/rendered/*.png` — the evidence, not
just a claim.

## What's implemented today

| Format | Inspect | Structural verify | Render (visual evidence) | Mutating operations |
|---|---|---|---|---|
| PDF | ✅ | ✅ | ✅ (`pypdfium2`) | `metadata_set`, `merge` |
| PPTX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set` |
| DOCX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set` |
| XLSX | ✅ | ✅ | ✅ (LibreOffice + `pypdfium2`) | `metadata_set` |
| HTML / SVG / Image | planned | planned | planned | — |
| HTML / SVG / Image | planned | planned | planned | — |

Unimplemented formats fail loudly with `ARTIFACT_ADAPTER_NOT_IMPLEMENTED`
— never a silent no-op. See `docs/roadmap.md` for the phase plan and
`docs/architecture.md` for why the Core/Adapter split makes adding a
format additive, not a rewrite.

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
pip install -e ".[pdf]"
artifact-skill doctor
```

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
stdio, schema-identical to the CLI.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — Core/Adapter split, the
  lifecycle, why formats are additive.
- [`docs/contract.md`](docs/contract.md) — the machine-readable contract
  schema, in full.
- [`docs/verification.md`](docs/verification.md) — the six-state
  verification model and how aggregation works.
- [`docs/security.md`](docs/security.md) — path/zip/subprocess safety
  model, resource limits, network policy.
- [`docs/research.md`](docs/research.md) — prior art and why this doesn't
  duplicate Anthropic's own document skills.
- [`docs/roadmap.md`](docs/roadmap.md) — phase plan for DOCX/PPTX/XLSX/
  HTML/SVG.

## License

MIT
