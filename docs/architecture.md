# Architecture

## What this is (and isn't)

`artifact-skill` is not a document generator, not a video editor's cousin,
and not a wrapper around one Office format. It is an **execution and
verification engine**: given an artifact (a file that claims to be a
PDF/DOCX/PPTX/XLSX/HTML/SVG/image) and a request, it inspects what the file
actually is, plans a mutation, executes it without touching the original,
renders it to something an agent can look at, checks it structurally, and
emits a machine-readable receipt. See `README.md` for the pitch and
`docs/research.md` for why nothing else already does this combination.

## Layering

```
                    User
                      |
                      v
                  AI Agent
                      |
                      v
                 SKILL.md
                      |
              +-------+-------+
              |               |
             CLI             MCP
              |               |
              +-------+-------+
                      |
                      v
              Artifact Core
                      |
       +--------------+--------------+
       |              |              |
    Inspect        Execute        Verify
       |              |              |
       +--------------+--------------+
                      |
                  Adapters
       +------+------+------+------+------+
       |      |      |      |      |      |
      PDF   PPTX   DOCX   XLSX   HTML   SVG
       |
       v
   Local Backends (pypdf, pypdfium2, ...)
       |
       v
Render / Evidence
       |
       v
Production Receipt
```

`cli/main.py` and `mcp/server.py` are both thin: they parse a request into
the same core calls (`core/engine.py`, `adapters/registry.py`,
`doctor/detect.py`) and format the same result two different ways. Neither
layer contains business logic the other doesn't share — this is what keeps
them from drifting, on top of the contract test that checks their schemas
match.

## Core abstractions (`src/artifact_skill/core/`)

| Module | Owns |
|---|---|
| `artifact.py` | `ArtifactRef` (path + content-sniffed type + sha256 + size), `InspectionReport` |
| `operation.py` | `OperationPlan` (what `execute` *would* do), `OperationRecord` (what it *did*), `default_output_path` (Original Protection) |
| `capability.py` | `Capability`, `CapabilityStatus` (AVAILABLE/MISSING/UNKNOWN/NOT_REQUIRED/NOT_IMPLEMENTED), `CapabilityReport` |
| `verification.py` | `Check`, `CheckStatus` (PASS/WARN/FAIL/UNKNOWN/NOT_CHECKED/SKIPPED), `VerificationResult`, worst-status-wins `aggregate()` |
| `contract.py` | `ToolContract`, the `TOOLS` list — single source of truth for CLI + MCP |
| `errors.py` | `ArtifactError` and subclasses, the category → exit-code map |
| `engine.py` | `run_lifecycle()` — the inspect→plan→execute→render→verify→fix loop |

Core never imports a format-specific library (no `import pypdf` outside
`adapters/pdf/`). This is enforced by convention today; a lint rule that
greps for it is a natural Phase-2 addition once there is a second adapter
to check the rule against.

## Adapters (`src/artifact_skill/adapters/`)

`adapters/base.py` defines `ArtifactAdapter`:

```
detect(ref) -> bool             # classmethod, content-based
operations() -> {name: OperationSpec}   # mutating ops + their verification policy
capabilities() -> [Capability]  # probed right now, not assumed
inspect(ref) -> InspectionReport
plan(ref, op, args, output_path) -> OperationPlan       # pure, no I/O
execute(ref, op, args, output_path) -> ArtifactRef       # writes only to output_path
render(ref, out_dir) -> RenderResult                     # optional; default raises capability error
verify_structural(ref, policy) -> VerificationResult
fix(ref, op, args, failed_result) -> dict | None         # optional; None = no fixer
refine_structural_with_render(structural, render) -> VerificationResult  # optional; default no-op
```

`refine_structural_with_render()` (Issue #14) lets an adapter upgrade a
structural check that's honestly `UNKNOWN` on its own into something real,
using a render that already happened as part of the same
`execute`/`receipt` lifecycle run — DOCX's `page_count` is the first user
(see `docs/adapters.md`'s DOCX section). It never causes rendering to
happen; `core/engine.py` only calls it with whatever `RenderResult` (or
`None`) the run already produced, and the default is a no-op.

Adding a format means implementing this interface under `adapters/<format>/`
and registering it in `adapters/registry.py`. Nothing in `core/`, `cli/`, or
`mcp/` changes. Every `ArtifactType` this project currently knows about
(PDF, PPTX, DOCX, XLSX, PNG/JPEG/WebP, HTML, SVG) has a real adapter as of
`docs/roadmap.md`'s Phase 6 — `registry.py`'s `_PLANNED` map is empty. It
still exists, and still matters: a future format added there before its
adapter lands makes `get_adapter()` raise a clear
`ARTIFACT_ADAPTER_NOT_IMPLEMENTED` instead of `KeyError` or, worse, a
fabricated success (`tests/unit/test_registry.py` exercises this path via
a temporary monkeypatched entry, since no real one currently exists).

### Why PDF first, and why pypdf + pypdfium2

See `docs/research.md` §5 for the license comparison. In short: PyMuPDF is
the fastest/easiest PDF rendering library but is AGPL-3.0, which is a real
constraint for a tool meant to be embedded in other people's (possibly
closed-source) agent pipelines. `pypdf` (BSD-3) covers structural
read/write/merge; `pypdfium2` (Apache-2.0/BSD dual) covers rendering via
Google's PDFium engine, with no external binary to install — which also
serves local-first better than shelling out to Poppler.

## Lifecycle (`core/engine.py`)

`run_lifecycle()` implements: inspect → plan → execute → render →
structural verify → visual evidence → fix (bounded retries) → receipt.

Two things are deliberately *not* automated:

1. **Visual judgment.** The engine renders pages and records that evidence
   exists (`VerificationResult(kind="visual", inspected_by=None, ...)`). It
   never sets `inspected_by="agent"` or a pass/fail verdict on its own —
   only an agent that actually looked at the rendered PNGs can do that (see
   "Brain/Hands split" below).
2. **Fixing.** `ArtifactAdapter.fix()` defaults to `None` (no fixer) — most
   adapters still don't override it, since most structural failures have
   no safe automatic correction, and pretending one exists would violate
   spec's "no hallucinated success" rule. `PdfAdapter` is the one
   exception (Issue #8): its `fit_page_size` operation pairs with a
   `fix()` that corrects exactly one evidence-backed failure shape (a
   page-size-policy mismatch) and returns `None` for anything else. The
   loop architecture (`tests/unit/test_engine_lifecycle.py`) and this one
   real fixer (`tests/unit/test_pdf_adapter.py`) are both tested end to
   end — see `docs/roadmap.md`'s "Fix-loop honesty note" for the design
   rationale. Any other adapter can add its own fixer the same way,
   without touching `engine.py`.

## Brain / Hands split

This engine never makes a subjective call ("this design is good," "this
slide is the most important one"). It produces objective state (page
counts, encryption, extractable text, rendered pixels) and evidence files.
Whether a rendered page "looks right" is left to whoever is capable of
seeing it — an agent inspecting the PNGs, or a human. The receipt's
`verification.visual.inspected_by` field exists specifically to record
*who* reached a visual verdict, so a receipt can never quietly imply the
engine did.

## Receipt (`receipt/model.py`)

Schema `artifact-receipt/v1`. See `docs/contract.md` for the full field
list. Overall `status` is the worst-status-wins aggregate of: any failed
operation, the structural result, and the visual result. This means a
receipt's `status` can be `UNKNOWN` even when nothing outright failed — see
`docs/verification.md` for why that's intentional rather than a bug to
work around by hiding the unknown check.

## Security model

See `docs/security.md` for the full model; the short version: every
subprocess call goes through `security/subprocess_exec.py` (argv-only,
explicit allowlist, timeout, minimal env, isolated cwd); every filesystem
write goes through `security/paths.py` (atomic writes, path-escape checks);
zip-based formats (all of PPTX/DOCX/XLSX, and PDF's own occasional embedded
archives) are extracted through `safe_extract_zip()` with member-count,
total-size, and compression-ratio limits.

## What's deliberately out of scope for the MVP

See spec §62 / `docs/roadmap.md`: no cloud storage, no SaaS dashboard, no
custom LLM, no custom OCR/Office/PDF/browser renderer built from scratch,
no automatic internet content collection, no unlimited auto-fix loop
(bounded by `max_fix_iterations`, default policy is "stop and report" when
no fixer exists), and no subjective quality judgment inside Core.
