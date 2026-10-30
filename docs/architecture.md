# Architecture

## What this is (and isn't)

`artifacts-skill` is not a document generator, not a video editor's cousin,
and not a wrapper around one Office format. It is an **execution and
verification engine**: given an artifact (a file that claims to be a
PDF/DOCX/PPTX/XLSX/HTML/SVG/image) and a request, it inspects what the file
actually is, plans a mutation, executes it without touching the original,
renders it to something an agent can look at, checks it structurally, and
emits a machine-readable receipt. See `README.md` for the pitch and
`docs/research.md` for why nothing else already does this combination.

## This tool does not define "correct" — policy does, and policy is opt-in

A deliberate design decision, not an oversight: `core/engine.py` never
hardcodes what a valid PDF/PPTX/DOCX/... looks like. `verify_structural(ref,
policy)` always takes a plain `policy: dict` and every adapter reads it via
`.get(key)`/`"key" in policy` — never an assumption that a key is present or
meaningful (see `policies.py`'s module docstring). The engine's job is
running whatever policy it's handed against the artifact's real structure;
deciding *what that policy should require* is the caller's job, because
"correct" is use-case-dependent in a way this project cannot know in
advance — A4 with embedded fonts and no encryption is "correct" for a print
submission and irrelevant for a web page; a required `<title>` matters for
an HTML deliverable and not for CSV data. Baking any of that into the
engine itself would mean this tool silently deciding what's acceptable for
every caller, for every format, forever — the opposite of the six-state
verification model's whole point (`PASS`/`WARN`/`FAIL`/`UNKNOWN` come from
checking a *stated* requirement against reality, not from this project's
own opinion of what a document should look like).

The practical consequence, stated plainly because it is easy to miss: the
default policy (`{}`) barely gates anything — with no policy at all,
roughly the only thing that reliably fails is a structural defect like
zero pages (see `docs/verification.md`). `policies.py`'s named presets
(`print-a4`, `web-no-external`, ...) exist as ready-made starting points
precisely because assembling a real policy by hand, every time, is too
easy to skip. `execute`/`receipt` completing without `--policy`/
`--policy-preset` is not a false claim of correctness — it is an honest
report against an empty requirement — but it is also a real, open UX gap
(tracked in
[Issue #37](https://github.com/kajisho5/artifacts-skill/issues/37)):
nothing in the engine or CLI currently makes that omission visible to
whoever is reading the result. SKILL.md tells an agent to always pass a
policy; nothing enforces it.

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
       +------+------+------+------+------+------+------+------+------+------+
       |      |      |      |      |      |      |      |      |      |      |
      PDF   PPTX   DOCX   XLSX  Image  HTML   SVG   CSV    MD   EPUB  Media
       |
       v
   Local Backends (pypdf, pypdfium2, ...)
       |
       v
Render / Evidence
       |
       v
Artifact Verification Receipt
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
`adapters/pdf/`). This is still enforced by convention only, not by a
lint rule — CI runs `ruff`/`mypy` (Issue #13) but neither has a
project-specific rule for this particular invariant configured. Seven
adapters now exist to check the rule against, so "once there is a second
adapter" (this sentence's original framing) is long past; a static check
(`ruff`'s `TID251` banned-api rule, or a small custom AST grep in CI)
remains a real, not-yet-done addition, not something the current ruff/
mypy step already covers for free.

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
(PDF, PPTX, DOCX, XLSX, PNG/JPEG/WebP, HTML, SVG, CSV, Markdown, EPUB,
Media) has a real adapter — `registry.py`'s `_PLANNED` map is empty. It
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

`operation` is optional (Issue #18): passing `None` takes a separate,
shorter branch — inspect → render → structural verify → receipt, with no
`execute()` call and no fix loop (there is no operation to retry). This is
the only route to a real Artifact Verification Receipt for a format with zero
mutating operations (HTML, SVG currently) — before this, the flagship
`receipt` command was unusable for those two formats entirely, and an
agent had to hand-assemble evidence from separate `inspect`/`render`/
`verify` calls. `_run_verify_only_lifecycle()` is a small, separate
function rather than a branch threaded through the main loop, so the
mutating path's structure (and its tests) are untouched by this addition.

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

## Evidence directory per run (Issue #47)

**Context.** `render()` writes fixed file names (`rendered/page-001.png`,
`rendered/frame-001.png`, ...) and the receipt is always `receipt.json`.
Until this change, every CLI/MCP call that omitted `--evidence-dir` /
`--out-dir` (`evidence_dir` / `out_dir` over MCP) wrote into the same
cwd-relative `./reports`. Concurrent calls therefore overwrote each
other's evidence: with 50 concurrent `receipt` processes on two different
inputs, every receipt pointed at page images another process had written
for the other input, and only one `receipt.json` was left on disk.

**Decision.** When the caller omits the directory, each call gets a fresh
directory of its own, `./reports/<run-id>/`, where `<run-id>` is
`<UTC timestamp>-<8 random hex chars>` (e.g. `20261030T120501Z-3f9a0c1b`;
they sort in creation order). `core/run_dir.py` reserves it with a single
`mkdir()` that fails if the name exists, which is atomic, so two processes
can never be handed the same directory; on a clash it simply draws a new
name. Inside the run directory the layout is unchanged:

| Command | Default before | Default now |
|---|---|---|
| `receipt`, `execute` | `reports/receipt.json`, `reports/rendered/` | `reports/<run-id>/receipt.json`, `reports/<run-id>/rendered/` |
| `render` | `reports/rendered/` | `reports/<run-id>/rendered/` |
| `look` | `reports/contact-sheet.png`, `reports/rendered/` | `reports/<run-id>/contact-sheet.png`, `reports/<run-id>/rendered/` |

An explicit directory is used exactly as given, as before: a caller that
needs a known or reusable location passes one. Dry runs report a fresh
path but create nothing. A call that fails before writing anything (e.g.
a missing input) removes its empty run directory again.

**How a caller finds the run directory.** `receipt` already returns the
whole receipt (and the human report prints the `receipt.json` path); every
evidence path in the receipt's `artifacts` lives in the run directory.
`execute`'s JSON result gains an `evidence_dir` key. `render` and `look`
return the paths of the files they wrote, as before. The receipt itself
(`artifact-receipt/v1`) is unchanged.

**Alternatives considered.** `tempfile.mkdtemp()` gives the same
atomicity but creates the directory with mode `0700`, a visible change for
anyone who reads evidence as another user; plain `mkdir()` keeps the
umask-derived permissions `./reports` always had. A `reports/latest`
pointer was left out: updating it is itself a shared write that concurrent
runs would race on, which is the bug being fixed. Keeping `reports/` as
the default and only adding a lock would serialize runs and still leave
earlier receipts pointing at overwritten files.

**Consequences.** `./reports` now grows by one directory per run that
omits the directory; deleting old run directories is the caller's call
(`reports/` is already git-ignored). Callers or scripts that read the
fixed `reports/receipt.json` path should pass `--evidence-dir reports`
(the old behaviour, safe when runs are not concurrent) or read the path
from the result. Regression test: `tests/integration/test_evidence_dir_per_run.py`
runs 50 concurrent `receipt` processes and checks no evidence is shared,
lost or overwritten.

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

The Artifact Verification Receipt (`ArtifactVerificationReceipt`), schema
`artifact-receipt/v1`. See `docs/contract.md` for the full field
list. Overall `status` is the worst-status-wins aggregate of: any failed
operation, the structural result, and the visual result. This means a
receipt's `status` can be `UNKNOWN` even when nothing outright failed — see
`docs/verification.md` for why that's intentional rather than a bug to
work around by hiding the unknown check.

## Receipt name (ecosystem roadmap step 2)

**Context.** Until this change the receipt was called the "Production
Receipt" in the docs, the CLI/MCP help and the JSON Schema title, and the
Python type was `ProductionReceipt`. The AI Video Production OS
orchestrator, video-production-agent, has its own `ProductionReceipt`
(its ADR-040, Artifact type `PRODUCTION_RECEIPT`): a roll-up of one whole
production run, with different fields. Once the agent calls this skill,
both documents sit side by side in one run and the shared name makes them
easy to confuse.

**Decision.** The receipt is now called the **Artifact Verification
Receipt**, and the Python type is `ArtifactVerificationReceipt`. Renamed:
the docs (README, SKILL.md, `docs/`), the `receipt` command's CLI help and
contract/MCP description, the JSON Schema `title`/`description`, and the
type. Not renamed, because they are machine-readable and already emitted
or referenced by consumers:

| Identifier | Value (unchanged) |
|---|---|
| `schema` field in every receipt | `artifact-receipt/v1` |
| JSON Schema file and `$id` | `schemas/artifact-receipt-v1.schema.json` |
| Field names | `schema`, `status`, `input`, ... (all 13) |
| CLI command / MCP tool | `receipt` |
| Contract capability | `document.receipt` |
| File name | `receipt.json` |

The identifier `artifact-receipt/v1` never had the clash (it does not
contain "production"), so a receipt written before this change and one
written after are the same document, and a consumer that checks
`schema == "artifact-receipt/v1"` or validates against the published
schema needs no change. `ProductionReceipt` stays importable from
`artifact_skill.receipt` and `artifact_skill.receipt.model` as an alias of
`ArtifactVerificationReceipt` (the same class object, so `isinstance`
checks keep working). It does not raise a deprecation warning; new code
should use the new name.

**Alternatives considered.** Changing the identifier to
`artifact-verification-receipt/v1` would match the new name, but every
receipt already written says `artifact-receipt/v1`, and the published
schema pins that value with `enum`. It would need a reader that accepts
both values and a schema that allows both, for no gain: the identifier is
not the part that clashes. If the receipt shape ever needs a breaking
change, `docs/contract.md`'s versioning rule applies (a `v2` alongside
`v1` for a deprecation window), and the new name can be adopted then.
Removing `ProductionReceipt` outright would break any Python caller that
imports it, for the same lack of gain.

**Consequences.** Text that says "Production Receipt" now means only the
agent's document. Tests: `tests/unit/test_receipt_model.py` checks the new
type, that the alias is the same class, and that the emitted identifier is
still `artifact-receipt/v1`; `tests/schemas/test_schema_files.py` checks
the schema title uses the new name and that the `schema` enum still accepts
the old identifier.

## Captions (ecosystem roadmap step 4)

**Context.** The AI Video Production OS ownership map
(`AI-video-production-OS/ecosystem.json`) gives every job to exactly one
repository. This skill owns 生成文書の検証 (verifying generated documents).
Captions are owned elsewhere: subtitle-skill owns 字幕生成・検証 (subtitle
generation and validation, SRT/WebVTT output, burn-in, translation and
per-locale formatting), and qc-skill owns 規格判定(QC), whose rules include
subtitle timing and malformed-cue checks against the delivered video. The
ecosystem audit listed "no SRT/VTT/ASS adapter for captions" as a gap here,
and the map's one-line role for this skill mentions captions, so the
question was whether to add a caption adapter or write down that captions
are not this skill's job.

**Decision.** No caption adapter. Caption files are left to subtitle-skill
and qc-skill. What this skill does and does not do with captions:

| | This skill | Owner |
|---|---|---|
| A caption script delivered as a document (DOCX, PDF, Markdown, ...) | Verifies it like any other document: structure, leftover placeholder text, rendered pages | — |
| A video file that carries captions | Media adapter verifies the container and its video/audio streams; subtitle streams are not read | — |
| A standalone `.srt` / `.vtt` / `.ass` file | Not recognized: `inspect` returns `ARTIFACT_TYPE_UNSUPPORTED` | subtitle-skill |
| Writing, rewriting, translating or burning in captions | No | subtitle-skill |
| Cue timing, overlaps, reading speed, cues past the end of the video | No | subtitle-skill (document), qc-skill (against the delivered video) |

The contract states the same in a `not_provided` list, the OS registry's
key for a skill's self-declared non-responsibilities
(`core/contract.py::NOT_PROVIDED`). Each caption entry names its owner.
No capability id, `provides[]` entry or artifact type mentions captions.

**Alternatives considered.** An SRT/WebVTT/ASS adapter here (parse cues,
check timing, render a contact sheet of cues) would fill the audit's gap,
but it would give one job to two repositories, which the ownership map
forbids, and duplicate subtitle-skill's typed `SubtitleDocument`
validation with different rules, so the two could disagree on the same
file. A thin adapter that calls subtitle-skill would add a dependency
between two skills for no new check; the orchestrator
(video-production-agent) can call subtitle-skill directly. Reading
subtitle streams in the Media adapter was rejected for the same reason:
media facts already duplicate qc-skill and media-analysis-skill (see
`ECOSYSTEM_ROADMAP.md`, "Boundaries"), and this would widen that overlap.

**Consequences.** A caption file sent to this skill fails loudly with
`ARTIFACT_TYPE_UNSUPPORTED` rather than passing an empty check, and the
contract tells a caller where to go instead. The roadmap's owned-job note
no longer lists a caption adapter as missing. If the ownership map ever
gives caption files to this skill, this decision is reopened and an
adapter is added like any other format (`docs/adapters.md`). Tests:
`tests/contract/test_caption_boundary.py` checks that the contract claims
no caption capability, that `not_provided` names subtitle-skill and
qc-skill, and that `.srt`/`.vtt`/`.ass` files are detected as `unknown`
and rejected with `ARTIFACT_TYPE_UNSUPPORTED`.

## Dependency floors (supply-chain audit)

**Context.** A supply-chain audit (Trail of Bits supply-chain-risk-auditor,
checked against OSV on 2026-10-30) found that the optional extras allowed
vulnerable releases: `pypdf>=5.0` allowed 60 advisories (13 HIGH, mostly
long runtimes or memory exhaustion on crafted PDFs), and `Pillow>=10.0`
allowed 50+ (5 CRITICAL decoder heap overflows). The PDF adapter and every
renderer open files this skill is asked to verify, so these are the inputs
an attacker would control. The dev extra's `pytest>=8.0` allowed
GHSA-6w46-j5rx-g56g (tmpdir handling).

**Decision.** Raise each floor to the first release with no published
advisory: `pypdf>=6.21.2`, `pytest>=9.0.3` and `Pillow>=13.0.0`. Pillow
13.0.0 requires Python 3.11, and this skill supports and tests 3.10, so
Pillow's floor is split with an environment marker: Python 3.11+ gets
`>=13.0.0`, Python 3.10 gets `>=12.3.0` (the last release for 3.10).

**Consequences.** On Python 3.10 the Pillow advisories fixed in 13.0.0
remain (e.g. GHSA-332p-7hrc-92wf, GHSA-5x8w-8jj5-q52h, GHSA-8g5g-8vmr-9782,
GHSA-w889-685w-c8fh); a caller who needs the fully patched Pillow runs
Python 3.11+. Python 3.10 support is not dropped for a dependency. pypdf
and pytest floors apply on every supported Python.

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
