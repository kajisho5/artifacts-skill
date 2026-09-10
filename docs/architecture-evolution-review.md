# artifacts-skill Architecture Evolution Review

**Scope of this document:** review only. No implementation. No code in this
repository was changed to produce this document. Every code claim below is
backed by direct source reads (this review, and four independent
fresh-context research passes over `core/`, all 11 adapters,
`security/`+`rendering/`, and `cli/`+`mcp/`+`contract/`+`tests/`) plus a full
read of `SKILL.md`, `README.md`, and every file under `docs/` except
`docs/testing.md`, which does not exist (confirmed: `ls docs/testing.md` →
"No such file or directory"). All 23 named GitHub issues (#8, #18, #20,
#21, #24–#32, #36–#42, #44, #45, #47) were read directly, not assumed.

---

## 1. Executive Summary

The question posed was whether `artifacts-skill` — currently an **Artifact
Execution & Verification Engine** — should evolve into an **Artifact
Lifecycle & Verification Engine** with a `Discovery → Structure →
Relationship → Lifecycle → Execution → Verification → Impact → Evidence`
pipeline, introducing Artifact Relationship/Dependency/Lineage graphs and
Impact Analysis.

**The premise needs correcting before the question can be answered
honestly.** "Relationship discovery" is not greenfield. It already exists,
independently implemented six times, with an identical shape, across six of
eleven adapters (PPTX, DOCX, HTML, SVG, Markdown, EPUB — §8, §12). What does
**not** exist, anywhere in this codebase, is any concept spanning **two
independently-tracked `ArtifactRef` instances**. Every existing reference
check is strictly intra-artifact: does a reference resolve *inside this one
zip*, or *relative to this one file's own directory*. Nothing in `core/`,
`cli/`, or `mcp/` has ever taken, returned, or reasoned about more than one
file per call (§4, §12).

The proposed evolution conflates two very different things: (a)
consolidating an already-proven, already-working intra-artifact pattern,
which is real, low-risk, low-cost maintenance work; and (b) building a new
cross-artifact graph engine with autonomous reference-following, a new
object model (artifact sets), new I/O class (directory scanning / following
references the caller never explicitly named), and new CLI/MCP/receipt
surface area — which is a substantial new subsystem, not an extension of
what exists.

**Recommendation: NARROW. Confidence: High.** See §32 for the full
reasoning and the concrete narrow scope that *is* justified by the evidence.

---

## 2. Current Architecture

Confirmed layering (`docs/architecture.md`, verified against `cli/main.py`,
`mcp/server.py`, `core/*.py`, `adapters/*/adapter.py`):

```
User → AI Agent → SKILL.md
                     |
            +--------+--------+
            |                 |
           CLI               MCP
     (cli/main.py)     (mcp/server.py)
            |                 |
            +--------+--------+
                     |
              core/contract.py   (single hand-written TOOLS list;
                     |             artifact_types/capabilities fields
                     |             computed from adapters/registry.py)
                     v
              core/engine.py
        (build_plan / run_lifecycle /
         _run_verify_only_lifecycle)
                     |
          +----------+----------+
          |          |          |
     adapter.plan  .execute  .verify_structural / .render
          |
   adapters/registry.py → one ArtifactAdapter per ArtifactType
   (pdf, pptx, docx, xlsx, image, html, svg, csv, markdown, epub, media)
          |
   Local backends (pypdf/pypdfium2, python-pptx/docx/openpyxl + LibreOffice,
   Pillow, Playwright+Chromium, ffmpeg/ffprobe)
```

`cli/main.py` and `mcp/server.py` are both thin dispatchers onto the same
`core/engine.py` calls; neither contains logic the other lacks
(`docs/architecture.md` §Layering, confirmed by direct read of both files —
Part A/B of the CLI/MCP research pass).

Nine tools exist, one call each: `doctor, contract, inspect, plan, execute,
render, verify, look, receipt`. **Every one of them takes exactly one
positional `input` path.** There is no subcommand, flag, or MCP tool
anywhere in the current surface that accepts more than one artifact, a
directory, or a glob.

---

## 3. Current Responsibility Boundary

- **`core/`** never imports a format-specific library (enforced by
  convention only — not by a lint rule; `docs/architecture.md` says a
  `ruff` `TID251` rule or custom AST check "remains a real, not-yet-done
  addition"). `core/` owns: `ArtifactRef` construction, the
  plan→execute→verify→fix loop, the contract, capability reporting, error
  taxonomy, verification aggregation.
- **`adapters/<format>/adapter.py`** owns everything format-specific:
  detection, mutating operations, structural checks, rendering. This is
  where all six existing intra-artifact reference-resolution
  implementations live (§8) — **none of them are in `core/`.** This is
  itself a strong, already-established precedent: when this codebase
  needed "does a reference resolve" logic, it built it six separate times
  inside adapters, never once inside `core/`.
- **`security/`** and **`rendering/`** are shared *utility* layers adapters
  call into (subprocess execution, path safety, zip safety, XML-entity
  guards, LibreOffice/Chromium/ffmpeg invocation) — not orchestration.
- **`cli/`** and **`mcp/`** are presentation-only, generated-schema-checked
  (§13/§14 below) dispatchers onto `core/engine.py`.

This split is the reason the project's own claim ("adding a format means
adding an adapter; nothing in `core/`, `cli/`, or `mcp/` changes" —
`docs/architecture.md`) is actually true today, confirmed by the adapter
survey: 11 formats, 0 format-specific imports found in `core/`.

---

## 4. Current Data Model

All fields below are quoted from source, not paraphrased (full detail in
the research agents' reports; summarized here with file references).

| Concept | Fields | Created by | Consumed by | Layer | Mutable? | JSON? | CLI/MCP-visible? | Adapter-specific or core? |
|---|---|---|---|---|---|---|---|---|
| `ArtifactRef` (`core/artifact.py:329`) | `path, type, sha256, size_bytes` — **exactly 4 fields, no relationship/parent/embeds field of any kind** | `ArtifactRef.from_path()` | every adapter method, `engine.py` | core | `frozen=True` (immutable) | via `to_dict()` | yes (`inspect`'s `artifact` key) | core |
| `InspectionReport` (`core/artifact.py:395`) | `artifact: ArtifactRef`, `details: dict` (free-form, adapter-populated), `warnings: list[str]` | `adapter.inspect()` | CLI/MCP `inspect` output only — **never consumed by `run_lifecycle()`** (§5) | core envelope, adapter-owned payload | mutable dataclass | yes | yes | mixed — envelope is core, `details` schema is 100% adapter-defined and untyped |
| `OperationPlan` (`core/operation.py:16`) | `operation, adapter, input, output_path, required_capabilities, files_touched, files_created, rendering_strategy, verification_strategy, risks, warnings` (11 fields) | `adapter.plan()` (or synthesized directly in `engine.py` for the verify-only branch) | `engine.py::build_plan()`, `plan`/`execute --dry-run` CLI/MCP output | core | mutable | yes | yes | core-defined, adapter-populated |
| `Capability` / `CapabilityReport` (`core/capability.py`) | `Capability`: `id, status, detail, detected_via, version`. `CapabilityStatus`: `AVAILABLE, MISSING, UNKNOWN, NOT_REQUIRED, NOT_IMPLEMENTED` | `adapter.capabilities()`, `doctor/detect.py` | `engine.py` (gates execution), `doctor` CLI/MCP | core | `Capability` frozen; report mutable (dict-of-capability) | yes | yes | core |
| `Check` / `CheckStatus` / `VerificationResult` (`core/verification.py`) | `CheckStatus`: **exactly** `PASS, WARN, FAIL, UNKNOWN, NOT_CHECKED, SKIPPED` (six, confirmed by direct source read, `verification.py:40-50`). `Check`: `id, name, status, message, evidence`. `VerificationResult`: `kind, checks, evidence_files, inspected_by`; `.status` is a computed `@property` = `aggregate()`, never stored | `adapter.verify_structural()`, engine's visual-evidence step | `receipt/model.py`, CLI/MCP `verify` output, `refine_structural_with_render()` | core (status enum + aggregation), adapter (which checks exist) | `Check`/`VerificationResult` mutable pre-aggregation | yes | yes | core structure, adapter content |
| `RenderResult` (`adapters/base.py:41`) | `kind, files, backend, warnings` (4 fields) | `adapter.render()` | `engine.py`'s visual-evidence step, `refine_structural_with_render()`, `render`/`look` CLI/MCP | **adapter layer**, not `core/` | mutable | yes (`to_dict()`) | yes | adapter |
| `ProductionReceipt` (`receipt/model.py:32`, schema `artifact-receipt/v1`) | `schema, status, input, operations, verification, artifacts, warnings, limitations, environment, capabilities, timestamp, tool_version, iterations` (13 fields) | `ReceiptBuilder.build()` inside `engine.py`'s two lifecycle functions | `receipt`/`execute` CLI/MCP output, `schemas/artifact-receipt-v1.schema.json`-validated in CI | core | built once, then written atomically | yes, published JSON Schema | yes | core |
| `ToolContract` / `TOOLS` (`core/contract.py:54`) | 13 fields per tool (`name, description, input_schema, output_schema, artifact_types, capabilities, mutates_input, side_effects, dry_run_supported, verification_policy, visual_requirement, evidence, errors`) | hand-written literal list; `artifact_types`/`capabilities` fields computed from `adapters/registry.py` at call time | `mcp/server.py::build_tools_list()` (generated at runtime from `TOOLS`), `contract` CLI/MCP, the CLI/MCP-consistency test | core | effectively static (module-level list) | yes, published JSON Schema | yes | core, with adapter-registry-derived fields |
| `Limits` (`security/limits.py:14`) | 9 fields — `max_input_bytes, max_zip_members, max_zip_uncompressed_bytes, max_zip_compression_ratio, max_pages, subprocess_timeout_seconds, render_timeout_seconds, max_fix_iterations, max_video_pixels` | `DEFAULT_LIMITS = Limits()` | threaded through `render()`, `subprocess_exec.run()`, `check_input_size()`, `safe_extract_zip()`, media's pixel guard | `security/` | `frozen=True` | no (Python object only) | no direct CLI/MCP flag to override; internal only | core/shared |
| `ArtifactError` hierarchy (`core/errors.py`) | `ArtifactInputError, ArtifactCapabilityError, ArtifactExecutionError, ArtifactVerificationError, ArtifactSecurityError` → 5 of 6 `ErrorCategory` values have a matching subclass. **`ErrorCategory.INTERNAL` has an exit code (5) but no matching exception subclass defined in `errors.py`** — flagged as a real, minor asymmetry, not chased further in this review | raised throughout adapters/core | `cli/main.py`'s top-level handler, `mcp/server.py`'s dispatch boundary | core | n/a | `to_dict()` | yes (error JSON shape) | core |

**Verification Policy** is not a typed model at all — it is `dict[str,
Any]`, read exclusively via `.get(key)`/`"key" in policy` by every adapter
(`docs/architecture.md`: "This tool does not define 'correct' — policy
does"). `Limits`, by contrast, *is* a typed, frozen dataclass but has no
CLI/MCP-facing override surface today.

**The load-bearing fact for the rest of this review:** nothing in this
table has a field, method, or concept that spans two `ArtifactRef`
instances. `InspectionReport.details` is the only place a second artifact
*could* be referenced, and only as an untyped string inside adapter-defined
JSON (e.g. XLSX's `external_links: list[str]`, HTML's
`local_resources_missing: list[str]`) — never as a second `ArtifactRef`,
never resolved into a second `InspectionReport`.

---

## 5. Current Lifecycle

`docs/architecture.md` and `SKILL.md` both describe the lifecycle as
`inspect → plan → execute → render → structural verify → visual verify →
fix → receipt`. **This is not what `core/engine.py::run_lifecycle()`
actually does.** Read directly, function by function (confirmed by the
core-facts research pass, independently cross-checked against the file):

- `adapter.inspect()` **is never called inside `run_lifecycle()` or
  `_run_verify_only_lifecycle()`** — grepped exhaustively, zero call sites.
  `inspect` is a separate, standalone CLI/MCP tool that never participates
  in the `execute`/`receipt` lifecycle. The module docstring's own summary
  line, and `contract.py`'s tool description string, both advertise an
  `inspect` step the receipt-producing code path never performs.
- The real per-iteration order (mutating branch) is: `build_plan()` (which
  *does* call `adapter.plan()`, which typically calls `self.inspect(ref)`
  internally — so inspection happens, just inside `plan()`, not as an
  engine-level phase) → `adapter.execute()` → `adapter.verify_structural()`
  → (conditionally) `adapter.render()` inside `_visual_evidence_result()`
  → `adapter.refine_structural_with_render()` → retry-or-stop.
- A failed `execute()` **skips verification entirely** and breaks the loop
  — there is no "verify what partial output exists" step.
- `adapter.fix()` is called with the **original input `ref`**, never the
  just-produced (verification-failing) `output_ref` — a fixer only ever
  sees the pre-execute artifact.
- The verify-only branch (`operation=None`, Issue #18) calls
  `verify_structural()` directly on the original `ref`, never runs
  `execute()`, and hardcodes `iterations=1`.

This is a genuine, pre-existing doc/code mismatch, independent of anything
proposed in this review. It does not block or support the proposed
evolution either way, but it matters for §29 (documentation changes) and
for calibrating how much weight to put on any doc-stated "lifecycle" claim
versus the actual code.

---

## 6. Proposed Evolution

As stated by the requester (§5/§40 of the request): replace the six-stage
lifecycle with a nine-stage one — `Artifact/Artifact Set → Discovery →
Structure/References → Relationship/Dependency → Plan → Execute → Render →
Verify → Impact Analysis → Evidence/Receipt` — explicitly framed as a
hypothesis to be evaluated against the codebase, not a specification to
implement.

## 7. Why It Is / Is Not Needed

**Is needed, to the extent that:** a real, already-proven pattern
(intra-artifact reference resolution) is duplicated six times with
near-identical code and no shared helper — that is ordinary technical debt
worth addressing regardless of any larger evolution question (§8, §26).

**Is not needed, to the extent that:** the full nine-stage,
multi-artifact-graph vision requires capabilities this codebase has never
had (directory scanning, autonomous cross-file traversal, a plural artifact
object model) and the value of that specific capability — as opposed to
the narrower intra-artifact pattern already proven — is real for at most
6 of 11 formats and fabricated for the rest (§20, §32).

---

## 8. Artifact Relationship Model — what exists today

Direct, exhaustive per-adapter evidence (from the adapter research pass;
file:line citations preserved):

| Adapter | Reference concept exists? | Scope | Missing-target Check id | Status when missing | Ever follows/opens the target? |
|---|---|---|---|---|---|
| PDF | No (font embedding ≠ cross-file reference; `merge`'s `additional_inputs` are explicit new-content args, not discovered references) | — | — | — | — |
| PPTX | Yes — `shape.image.blob` resolves the OOXML relationship id for picture shapes (`pptx/adapter.py:244-248`) | intra-zip | `broken_media` | **FAIL, unconditional** (not policy-gated) | N/A (intra-zip) |
| DOCX | Yes — walks `document.part.related_parts` directly (`docx/adapter.py:218-227`) | intra-zip | `broken_media` | **FAIL, unconditional** | N/A (intra-zip) |
| XLSX | Yes — `wb._external_links` (`xlsx/adapter.py:231`) | cross-file (external workbook) | `external_links` | policy-gated FAIL/WARN — **presence only** | **No — explicitly not fetched**, message cites network-off-by-default |
| HTML | Yes — full resource-attr + CSS scan, resolves relative to file's own directory (`html/adapter.py:288-305`) | local filesystem, relative to this file | `local_resources` | **FAIL, unconditional** | Existence probe (`is_file()`) only, no content read |
| SVG | Yes — `image`/`use`/`script` href scan, same local-resolution pattern | local filesystem | `local_resources` | **FAIL, unconditional** | Existence probe only |
| CSV | **No — confirmed, exhaustively searched** | — | — | — | — |
| Markdown | Yes — `[text](url)`/`![alt](url)` regex, same local-resolution pattern | local filesystem | `local_resources` | **FAIL, unconditional** | Existence probe only |
| EPUB | Yes — manifest↔zip-member and spine↔manifest resolution (`epub/adapter.py:301-304`) | intra-zip | `manifest_references_resolve`, `spine_references_resolve` | **FAIL, unconditional**, both | N/A (intra-zip) |
| Image | No (EXIF orientation only; no provenance/reference field read at all) | — | — | — | — |
| Media | **No — confirmed** (container tags read only for placeholder-text scanning) | — | — | — | — |

**The pattern, stated precisely:** every existing "is this reference
broken" check in this codebase is **unconditional FAIL, never policy-gated**,
and every existing "does an external/cross-boundary reference exist"
check (XLSX external links, HTML/SVG/Markdown *external* — as opposed to
*local* — resources) is **policy-gated WARN/FAIL on presence alone, and
explicitly never resolved**, citing the network-off-by-default policy.
This is a clean, already-established two-tier precedent: *local/intra-
artifact → resolve and hard-fail if broken; anything crossing a trust
boundary → report presence, never follow.*

**What none of these six implementations do:** produce a second
`ArtifactRef`, call a second adapter's `inspect()`, or persist anything
beyond a single `Check`'s `evidence` dict (a list of strings). The
"relationship" is discovered, used to answer one boolean ("does this
resolve"), and discarded. There is no relationship *model* — no node, no
edge, no graph — anywhere in this codebase today.

### Terminology check (per the request's §6)

Applying the request's own vocabulary to what actually exists:
- `contains`/`embedded`: PPTX/DOCX/EPUB's intra-zip resolution is exactly
  this — a manifest/relationship part naming a member of the *same*
  container.
- `linked`/`external-reference`: XLSX's external-workbook links, and
  HTML/SVG/Markdown's *external* (http/https) resources.
- `runtime-reference`: HTML/SVG/Markdown's *local* file references are
  closer to this — resolved against the referencing file's own directory
  at inspection time, not baked into a container format's own manifest.
- `derived-from`/`generated-from`: **does not exist anywhere.** PDF's
  `merge` operation does not record, in the output file or anywhere else,
  which inputs produced it. Confirmed no provenance metadata is ever
  written by any adapter's `execute()`.
- `visual-reference`/`semantic-reference` (the request's own
  `presentation.pptx visually resembles logo.png` example): **does not
  exist and nothing in this codebase's design principles would tolerate
  it** — `docs/architecture.md`'s "Brain/Hands split" explicitly forbids
  the engine from making a subjective/visual judgment call on its own.
  This class of "relationship" is correctly excluded by the request's own
  §6/§8, and this review agrees it must stay excluded from any
  deterministic-facts model.

---

## 9. Dependency Model

No dependency model exists. The closest analog — XLSX's `external_links`
— reports *presence* of a dependency-shaped reference (a link to another
workbook) without ever validating it resolves, exists, or is itself valid.
There is no "this artifact depends on that artifact, and here is whether
that dependency is satisfied" concept anywhere.

If built, a dependency model would need, at minimum, to answer: is a
dependency *edge* symmetric with a *reference* edge, or a subtype of it?
The evidence in §8 suggests **reference and dependency are the same
concept in this codebase's existing precedent** — "PPTX shape references
this image" and "PPTX depends on this image existing" are not
distinguished today, and inventing a distinction not backed by any
existing adapter behavior would be speculative modeling, which §8/§39 of
the original request explicitly warns against ("推測を明確に禁止する").

---

## 10. Lineage Model

**Does not exist in any form.** No adapter's `execute()` writes anything
into its output that records what produced it. PDF's `merge` is the one
operation that structurally combines multiple inputs into one output, and
even there, `OperationPlan.files_touched` records the input paths only for
that single operation's own plan/receipt — nothing is written into the
merged PDF itself, and a later `inspect()` of that merged file cannot
recover which files were merged. Lineage, as the request's own §12
distinguishes it from dependency/reference, is **entirely unimplemented**,
not partially implemented — there is no partial version of this to build
on, unlike the reference-resolution pattern in §8.

---

## 11. Impact Analysis Model

**Does not exist.** No code path in this repository re-evaluates one
artifact's verification status in response to a *different* artifact
changing. This is a pure greenfield concept with respect to the current
codebase — there is no existing partial implementation, no adjacent
pattern, and no precedent for the request's own required distinction
(§11: `changed dependency ≠ invalid artifact`; a changed reference should
produce something like `requires_reinspection`/`potentially_affected`, not
an automatic `invalid`). Building this without first having a real,
persistent, multi-artifact relationship model (§8's gap) is not possible —
Impact Analysis is downstream of a capability that doesn't exist yet, which
is exactly why the phased plan in the original request (§28, Phase 7) puts
it last and gates it on "Graph が十分安定してから" (only once a graph is
stable) — a sequencing this review agrees with, whether or not the graph
itself is ultimately built (§32).

---

## 12. Core vs Adapter Boundary

Answering the request's own six questions (§13) directly, from evidence:

- **Q1 (Relationship graph in core?)** No. Every existing reference-
  resolution implementation lives in an adapter, never in `core/`. `core/`
  has zero knowledge of format-specific reference shapes (OOXML
  relationship IDs, EPUB manifests, Markdown link syntax) and the
  project's own "core never imports a format-specific library" principle
  would be directly violated by moving this logic there. **A graph
  *engine* (the generic node/edge/traversal machinery, format-agnostic)
  could theoretically live in `core/` without violating this principle —
  but nothing in the current codebase needs one, since no existing
  capability spans more than one artifact (§4's conclusion).**
- **Q2 (In adapter?)** Yes, for the intra-artifact pattern that already
  exists (§8) — this is where it already lives, correctly, six times over.
  A cross-artifact capability, if built, would need each adapter to expose
  a *discovery* primitive (e.g., "list the reference strings I found, and
  classify each as intra/local/external") while a **new, separate layer**
  (not existing `core/`, not any one adapter) resolves those strings into
  second `ArtifactRef`s and orchestrates recursive inspection. That
  separate layer is a new architectural component, not a natural extension
  of either `core/` or `adapters/` as they exist today.
- **Q3 (Separate package?)** Worth considering only if built at all (§32) —
  see §35's criteria; a graph/impact-analysis subsystem with its own
  security model (autonomous multi-file traversal) and its own performance
  characteristics (O(N+M) over a corpus vs. O(1) per single file) is
  exactly the shape of thing the request's own §35 criteria flag as
  "should probably be separate."
- **Q4 (Separate Skill?)** Same reasoning as Q3 — a "verify this one file
  is correct" skill and a "map how a whole project's files relate to each
  other" skill are different jobs with different risk profiles; conflating
  them risks exactly the "巨大Skill化" the request's own §34 forbids.
- **Q5 (Does this break artifacts-skill's existing philosophy?)** The
  *narrow* version (§32) does not — it's a policy-shaped extension of
  `verify_structural(ref, policy)`, the existing contract. The *full*
  graph/lineage/impact vision, as literally specified in the request's own
  §5/§40, would — it requires a new object model, a new I/O class
  (autonomous file-following the caller never named), and a materially
  different security posture (§16), none of which the existing philosophy
  ("caller explicitly names every file this tool touches," "core/ stays
  format-agnostic," "verification is opt-in policy, not engine opinion")
  currently accommodates.
- **Q6 (巨大Skill化 risk?)** Real and specific — see §34.
- **Q7 (Can users still understand the boundary?)** For the narrow version:
  yes — it's still "verify one file, now optionally against named related
  files the caller also supplies." For the full graph vision: no — "this
  tool also maps your whole project's dependency graph and tells you what
  might be affected by a change" is a materially different pitch than the
  current README's "closes the gap between 'I created the file' and 'the
  file is correct.'"

---

## 13. CLI Contract

No new CLI commands are justified by the evidence gathered. The request's
own draft commands (`graph`, `deps`, `impact`, `references`, `diff`) all
presuppose the multi-artifact object model that doesn't exist (§4). If the
narrow recommendation (§32) is adopted, **no new CLI verb is needed at
all** — it is a new, optional field inside the existing `--policy` JSON
blob that `verify`/`execute`/`receipt` already accept, consistent with how
every other format-specific policy key already works (`docs/architecture.md`:
"every adapter reads it via `.get(key)`," `core/engine.py`'s
`unknown_policy_keys()` guard already rejects typos in whatever new key
gets added the same way it rejects any other, per Issue #24).

---

## 14. MCP Contract

Same conclusion as §13, mechanically: if no new CLI verb is added, no new
MCP tool is added, and the existing CLI-flag ⟷ `input_schema` ⟷ MCP
`tools/list` consistency test
(`tests/contract/test_cli_mcp_consistency.py::test_cli_flags_match_input_schema_properties_in_both_directions`)
continues to hold with zero changes to its own logic — a new policy key
does not need a new schema property at the top level, since `policy` is
already a single opaque JSON object in every tool's `input_schema`.

---

## 15. Receipt / Evidence Model

The request's own §19 poses four options (A: integrate into receipt, B:
separate `artifact-graph.json`, C: receipt references graph, D:
independent). Given §32's narrow recommendation, **none of these apply** —
there is no new evidence artifact. If the *full* graph vision were ever
built later, this review's reading of the evidence favors option B/D
(separate, receipt-independent) over A/C, because:
- `ProductionReceipt`'s own aggregation semantics (`_overall_status()`,
  worst-status-wins across operation/structural/visual) are already
  precisely scoped to "what was checked about *this one* artifact in *this
  one* run" — folding in a graph spanning other artifacts and other runs
  would break the receipt's own internal consistency story (its `input` is
  one `ArtifactRef`, singular, throughout).
- The existing, unfixed Issue #47 (evidence-directory collision under
  concurrency) already threatens the *current*, single-artifact
  `reports/receipt.json`/`reports/rendered/*.png` outputs. Any new
  `graph.json` sharing the same default `reports/` directory would inherit
  the identical, already-known bug on day one.

---

## 16. Security Model

This section is the strongest evidence against building the full graph
vision now, independent of API/architecture concerns.

**The current threat model's core invariant:** the tool only ever touches
files the caller explicitly named. Confirmed by direct code read: every
CLI/MCP tool takes exactly one `input` path; the one place a *second* path
is ever opened is PDF's `merge` operation, and that second path is also
supplied explicitly by the caller as an operation argument, not discovered
by the tool itself.

**A relationship-discovery/graph feature inverts this invariant** — it
would, for the first time, have the tool decide *on its own, by reading
data inside an untrusted file* which other files to open next. This is a
qualitatively new attack surface, and several existing, real gaps make it
materially riskier than it would first appear:

- **`safe_extract_zip()` — the project's own real, tested zip-bomb/zip-slip
  guard — has exactly one production call site: EPUB's `render()`.**
  Confirmed by exhaustive grep: PPTX/DOCX/XLSX's own zip handling goes
  entirely through python-pptx/python-docx/openpyxl's internal `zipfile`
  usage, which this project does not wrap with any member-count,
  total-size, or compression-ratio guard. `docs/security.md`'s own
  framing — "Office-family... files are executable-adjacent... zip-bomb-
  shaped archives... this project treats them that way from the start" —
  is not actually backed by code for these three formats today. **This is
  a pre-existing gap, independent of anything proposed here** — but it is
  directly activated by any new code that walks a PPTX/DOCX/XLSX's zip
  structure itself (rather than through the existing, safe
  library-mediated `shape.image.blob`/`document.part.related_parts`
  calls) to build a relationship graph. The existing `broken_media` checks
  are safe *because* they never touch `zipfile` directly. A graph-building
  feature that does would need genuinely new security work, not reuse of
  what's there.
- **No image decompression-bomb guard exists at all** — confirmed no
  `width*height` check against any `Limits` field for PNG/JPEG/WebP,
  unlike the Media adapter's `max_video_pixels` guard. A discovery feature
  that recursively inspects every image an artifact references would run
  headfirst into this gap for every image it follows.
- **`resolve_within()` has exactly one call site** (inside
  `safe_extract_zip()`) and is explicitly not a general-purpose path-safety
  helper (its own docstring says so, after Issue #30 corrected a prior
  overclaim). A discovery feature resolving caller-relative or
  artifact-relative reference paths would need its *own* containment
  story — reusing the existing helper by name would repeat exactly the
  overclaim Issue #30 already had to fix once.
- **Recursive/cyclic references** (`A → B → A`, the request's own §16
  concern) have zero precedent to build on — none of the six existing
  reference checks ever traverse more than one hop (they check "does the
  reference resolve," never "and does *that* target's own references
  resolve too"). Cycle detection, huge-graph limits, and recursion depth
  caps would all be new code with no existing analog to model against.

**Conclusion for this section:** any relationship-discovery feature that
autonomously follows references must be built with a new, explicit
threat model and new security controls (zip-bomb guards actually applied
to PPTX/DOCX/XLSX, an image pixel-count guard, a real general-purpose path
-containment helper, recursion/cycle limits) — none of which can be
inherited "for free" from the current codebase's existing controls, most
of which are narrower in scope than their own documentation currently
claims.

---

## 17. Determinism

No existing precedent either way — nothing in the current codebase has a
synthetic/generated ID for anything model-facing. `ArtifactRef`'s only
identity-shaped field is `sha256` (content hash, inherently deterministic
per-content). `ProductionReceipt.timestamp` is the one clearly
non-deterministic field in the entire data model, and it is explicitly
never used as an identity key anywhere — confirmed by `docs/verification.md`'s
own framing of receipts as evidence-of-a-run, not identity records.

If a graph were ever built, the existing convention (content hash as
identity, never a synthetic UUID or a runtime timestamp) is the only
consistent precedent to extend — node identity should be
`(path, sha256)` or `sha256` alone, never a counter or timestamp-derived
ID. This is a design note for a future implementer, not something this
review needs to resolve now, since no graph is being recommended (§32).

---

## 18. Concurrency

Issue #47 (open, unfixed, filed by this same review lineage's own prior
session): `reports/` is a fixed, cwd-relative default shared by every
`execute`/`receipt`/`render` invocation, with no PID/timestamp/random
uniqueness — reproduced with a measured 50% evidence-collision rate across
50 concurrent `receipt` calls. This is the current system's own,
already-known concurrency debt, on the *simpler* single-artifact model.
Any new shared-output artifact (a `graph.json`, a cache of discovered
relationships) would inherit this exact, already-diagnosed problem on day
one unless #47 is fixed first or the new artifact is designed with its own
independent uniqueness scheme from the start. This is a concrete argument
for sequencing: fixing #47 is lower-risk, smaller-scope, already-scoped
prerequisite work that has nothing to do with the graph question and
should not wait on it.

---

## 19. Performance

`docs/performance.md`'s own convention (measured numbers, explicitly not
permanent, re-run the script, never a hardcoded claim) is a real discipline
this review will not violate by inventing numbers for a feature that
doesn't exist. What can be said from the existing, real measurements: every
current benchmark (`run_perf_benchmark.py`, `run_scaling_benchmark.py`) is
single-artifact-scoped — one file in, timed once. **Nothing in the current
benchmark suite measures a corpus of N files, and no synthetic multi-file
fixture directory exists anywhere in `tests/fixtures/`.** A graph feature
at the scale the request's own §25/§30 contemplates (10/100/1,000/10,000
files) would need an entirely new benchmark category from scratch, with
real, re-runnable measurements — not estimated numbers — before any
performance claim could be made about it. This review makes none.

---

## 20. Format Feasibility Matrix

Built entirely from the adapter survey's direct evidence (§8), not from
"should be possible" reasoning:

| Format | Explicit refs extractable today? | Embedded assets | External refs | Dependency definable? | Lineage definable? | Hash | Structure diff | Render diff | Deterministic | Security risk if extended | Impl. difficulty (extending existing pattern) | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PDF | No (no existing code) | No (font-embedding only, not cross-file) | No | Not today — would be new work, no precedent | No | Yes (sha256, existing) | Not implemented (no page-content-diff exists) | Not implemented | Yes, if built the way §8's pattern works | Low-medium (PDF has no zip-bomb-class risk; embedded-file streams could theoretically be huge) | High (no adapter code to build on at all) | Difficult — genuinely new |
| PPTX | **Yes** — `broken_media` already resolves picture-shape relationships | Yes (images, via existing check) | No (no external-workbook-style concept in PPTX) | Extending `broken_media`'s pattern to a real graph node/edge: Medium difficulty | No | Yes | Not implemented | Not implemented | Yes | **High if graph-building bypasses python-pptx and touches `zipfile` directly (§16)**; Low if it stays inside python-pptx's own resolution the way `broken_media` already safely does | Low-medium (extend existing pattern) | High confidence this specific capability is real and low-risk *if scoped to intra-file* |
| DOCX | **Yes** — `document.part.related_parts` walk already exists | Yes (images) | No | Same as PPTX | No | Yes | Not implemented | Not implemented | Yes | Same caveat as PPTX | Low-medium | High confidence, same caveat |
| XLSX | **Yes, but presence-only** — `external_links` never resolves | No embedded-image check at all (confirmed absent) | **Yes** — the one format with a real cross-*file* reference concept already | Yes — this is the closest existing thing to a real dependency edge in the whole codebase | No | Yes | Not implemented | Not implemented | Yes | Medium — actually resolving `external_links` would be the *first* place this tool ever opens a second file the caller didn't name | Medium (resolving, not just detecting, is new) | Medium — real value, but resolving external workbooks crosses the trust-boundary line §16 flags |
| HTML | **Yes** — full resource scan + local resolution already exists | Yes | Yes (detected, never fetched) | Yes, for local refs specifically | No | Yes | Not implemented | Not implemented | Yes | Low for local refs (already an existence-probe, no content read); the moment content is *read* (to recursively inspect the referenced file), Medium | Low (existing `local_resources_missing` list is already 90% of the discovery work) | High confidence, best-fit format alongside SVG/Markdown |
| SVG | Same as HTML, narrower resource-tag set | Yes | Yes | Yes | No | Yes | Not implemented | Not implemented | Yes | Same as HTML | Low | High confidence |
| CSV | **No — confirmed zero concept, no cell-as-reference logic exists** | No | No | Not applicable — no real precedent for what a CSV "reference" even means structurally | No | Yes | N/A | N/A | Yes | Low (nothing to extend) | High (fabricating a feature with no format basis) | **Low confidence any real feature exists here — do not force one** |
| Markdown | **Yes** — link/image local resolution already exists, closest analog to the request's own worked example (`good.md`) | Yes | Yes | Yes | No | Yes | Not implemented | Not implemented | Yes | Same as HTML | Low | High confidence |
| EPUB | **Yes** — the richest existing internal reference graph (manifest + spine) | Yes (intra-archive only) | No (EPUB spec has no external-file concept in scope here) | Yes, intra-archive only | No | Yes | Not implemented | Not implemented | Yes | Same zip-bomb caveat as PPTX/DOCX, but EPUB is the one format that *already* goes through `safe_extract_zip()` for render — so this format is actually the *safest* one to extend, not the riskiest | Low (already the most graph-shaped adapter in the codebase) | High confidence |
| Image (PNG/JPEG/WebP) | No (EXIF has no reference field read) | No | No | Not applicable | No | Yes | N/A | N/A | Yes | Low, but recall no decompression-bomb guard exists (§16) — recursively opening referenced images is exactly where that gap bites | Medium-High (fabricating a feature; EXIF provenance chains are a real but different, unimplemented concept) | **Low confidence — do not force one; the "photo.png used by 3 decks" use case has zero implementation precedent** |
| Media (video/audio) | **No — confirmed zero concept** | No | No | Not applicable (no HLS/DASH manifest-following exists) | No | Yes | N/A | N/A | Yes | Low (nothing to extend), but the existing `max_video_pixels` decompression-bomb finding (§16) is a direct warning about what happens when this format's declared-vs-actual size gap is *not* guarded — the same caution applies to any new capability here | High (fabricating a feature) | **Low confidence — do not force one, despite this being the format category the requesting user's own professional context (映像・音響・配信) would most want it for** |

**Reading this matrix honestly:** real, low-risk, extend-what-exists
feasibility clusters around exactly 6 formats (PPTX, DOCX, HTML, SVG,
Markdown, EPUB) — all six already have the underlying discovery code.
PDF, Image, CSV, and Media have **zero** existing precedent, and building
a "relationship" feature for them would be inventing a capability from
nothing, in formats where — per the request's own §8/§39 prohibition on
treating推測 (inference) as fact — there is no deterministic mechanism to
extract a real reference at all (a CSV cell is not a file path; a video
container's own tags carry no reference semantics ffprobe surfaces).

---

## 21. Error / UNKNOWN Semantics

The request's own §22/§23 concern (don't let "relationship extraction
unsupported" collapse into "no dependencies", i.e. a fake PASS) is already
correctly modeled by the *existing* six implementations, and should be
followed exactly if any narrow extension is built: a reference that can't
be classified stays `UNKNOWN`, never silently becomes "no reference found."
Every one of the six existing checks in §8 already does this correctly —
none of them treat "couldn't determine" as "found nothing to check." This
is not a new problem to solve; it's an existing discipline to preserve.

The request's §11 distinction (`changed dependency ≠ invalid artifact`) has
no code to check against, since no dependency-change-detection exists —
noted as a design requirement for if/when this is ever built (§11), not
something this review can verify empirically today.

---

## 22. Backward Compatibility

Not implicated. The narrow recommendation (§32) adds an optional policy
key to an already-optional, already-freely-extensible `policy: dict` —
zero schema version bump, zero CLI/MCP breaking change, consistent with
how every other adapter-specific policy key already works (Issue #24's
`unknown_policy_keys()` guard already handles a caller passing an
unrecognized key correctly, the same mechanism a new key would use). No
existing command's meaning changes.

---

## 23. Versioning

No version bump implicated by the narrow recommendation — it's additive to
an already-open-ended `dict` parameter, the same category of change as
adding any other adapter's `recognized_policy_keys()` entry, which this
project has done repeatedly without a contract-schema version bump. If the
full graph vision were ever pursued later, `artifact-contract/v1`'s
`schema` field would need a real look (new tool names in `TOOLS`, likely a
minor version at minimum, given no existing field changes meaning) — not
resolved here since nothing is being built.

---

## 24. Test Strategy

Not implicated by the narrow recommendation beyond ordinary per-adapter
unit tests for whichever specific policy key gets added, following the
exact pattern `tests/unit/test_*_adapter.py` already uses for every other
policy key. If the full graph vision were ever pursued, the required test
categories the request's own §29 lists (determinism, concurrency, missing
references, cycles, malformed input, path traversal) all have **zero
existing test infrastructure to build from** — every current test fixture
directory (`tests/fixtures/<format>/`) holds single, independent files;
there is no multi-file fixture directory anywhere in the corpus today
(confirmed — `tests/fixtures/` structure per the CLI/MCP/test research
pass lists only per-format single-file subdirectories).

---

## 25. Benchmark Strategy

Same conclusion as §19/§24 — nothing to benchmark for the narrow
recommendation (a policy-key check is already covered by the existing
per-format benchmark cases' timing characteristics, sub-millisecond for
text formats per `docs/performance.md`'s own measured table). A
multi-file-corpus benchmark, if ever needed, is new infrastructure this
review does not design, since no feature requiring it is being
recommended.

---

## 26. Documentation Changes

If the narrow recommendation is adopted: `docs/adapters.md` (the specific
adapter's policy-key list) and `docs/verification.md` (if the new check's
`Check` id/semantics are unusual enough to warrant a worked example, the
way DOCX's `page_count`/XLSX's `formula_recalculation` already are). No
change needed to `README.md`, `SKILL.md`'s frontmatter `description`
(risk of over-triggering, per Issue #39's already-filed packaging-gap
concern — this review deliberately does not add scope to that
description), `docs/architecture.md`'s Core/Adapter boundary, or any
schema file.

---

## 27. Migration Strategy

Not applicable — no schema, CLI, or MCP surface changes in the narrow
recommendation.

---

## 28. Implementation Phases

**Given the recommendation is NARROW (§32), the request's own 8-phase plan
(§28) does not apply as written** — Phases 2 through 7 all presuppose a
graph object model this review does not recommend building. The only
phase-shaped work this review actually endorses:

- **Phase 0 (this document).** Done.
- **Phase 0.5 — Consolidate, don't expand.** Extract the six duplicated
  intra-artifact reference-resolution implementations (§8) into a single
  shared helper — living in `adapters/` (e.g. a small
  `adapters/reference_checks.py` used by the six adapters that already
  have this logic), **not** `core/` — with no new Check semantics, no new
  policy keys, no new CLI/MCP surface. This is a pure de-duplication
  refactor of code that already exists and already works identically six
  times over. Low risk, real (if modest) maintenance value, zero API
  surface change.
- **Phase 1 (only if real user demand materializes) — opt-in
  cross-artifact policy check.** A caller-supplied policy key (exact shape
  to be designed if/when pursued) letting `verify_structural()` check a
  *caller-named* second artifact's hash/existence against the primary
  artifact's own reference to it — e.g., "this PPTX's embedded logo should
  match this exact file" — using the existing `additional_inputs`-style
  pattern PDF's `merge` already establishes for accepting extra,
  explicitly-named paths. No graph, no autonomous following, no new object
  model, no new CLI verb.

Everything past this (a real graph engine, lineage, impact analysis) is
explicitly **not** phased here, because building a phased plan for a
capability this review is not recommending would misrepresent the
review's own conclusion.

---

## 29. Non-Goals

Explicitly out of scope, regardless of future revisiting, unless a
materially different capability class emerges than what was evaluated
here:
- A generic, `core/`-level relationship/dependency/lineage graph type.
- Autonomous, caller-unaware following of references discovered inside an
  artifact (the tool deciding on its own which other files to open).
- Any relationship type based on visual/semantic similarity ("resembles"),
  per the request's own §6/§8 exclusion.
- New CLI verbs (`graph`, `deps`, `impact`, `references`, `diff`) at this
  time.
- Forcing a "relationship" concept onto PDF, Image, CSV, or Media, none of
  which have any existing deterministic mechanism to support one (§20).
- Impact Analysis of any kind — it has no capability to build on yet (§11).

---

## 30. Risks

- **Scope-creep / "巨大Skill化" risk is real, not hypothetical** — see §34.
- **Security regression risk if built carelessly** — see §16's zip-bomb
  and decompression-bomb gaps, which are currently latent (not activated
  by anything in the codebase today) but would be directly activated by
  naive graph-building code.
- **Sunk-cost risk in the other direction** — the six existing
  intra-artifact implementations are real, proven, and currently
  undocumented as a *pattern* (each adapter's own section in
  `docs/adapters.md` describes its own `broken_media`/`local_resources`
  check individually; nothing currently states that this is the same
  pattern implemented six times). Leaving this undocumented risks a
  seventh, slightly-different reimplementation the next time a format
  needs it — the Phase 0.5 consolidation in §28 exists specifically to
  close this risk, independent of the larger graph question.

---

## 31. Rejection Criteria

Per the request's own §35, the full graph/lineage/impact vision meets
multiple criteria that would justify treating it as a separate project if
ever built at all:
- ✅ Core responsibility is materially different (single-artifact
  verification vs. multi-artifact corpus mapping).
- ✅ Would become a genuinely independent capability (a general dependency
  graph over a filesystem is not inherently document-verification-shaped).
- ✅ Security model changes materially (§16 — autonomous file-following is
  a new threat class this project's current model doesn't contemplate).
- ✅ Performance characteristics are qualitatively different (O(1)
  single-file operations today vs. O(N+M) corpus operations).
- ⚠️ Format-adapter boundary: partially at risk — the *discovery*
  primitive could stay adapter-scoped (§12, Q2) without breaking the
  boundary, but the *graph orchestration* layer would be new and doesn't
  cleanly belong to either `core/` or any single adapter.

This is direct evidence supporting **NARROW-to-REJECT-of-the-full-vision**,
not evidence for building it as part of this project even in a separate
package right now — the request asked this review to decide PROCEED /
NARROW / DEFER / REJECT for *this* evolution question, not to greenlight a
future separate project speculatively.

---

## 32. Final Recommendation

# NARROW

**Confidence: High.**

**What to narrow to:** consolidate the six existing, already-proven,
already-identical intra-artifact reference-resolution implementations
(§8, §28 Phase 0.5) into one shared adapter-layer helper — a
straightforward de-duplication refactor with no API, schema, or behavior
change. If and only if real, demonstrated user demand emerges for a
cross-artifact check, extend it narrowly via an opt-in policy key
following the existing `additional_inputs`-style pattern PDF's `merge`
already establishes — never as an autonomous graph/discovery engine.

**What NOT to build, at this time:** the nine-stage
Discovery→Structure→Relationship→Lifecycle→...→Impact→Evidence pipeline,
any `core/`-level graph type, any new CLI verb, any autonomous
reference-following, and any relationship modeling for PDF, Image, CSV, or
Media (§20 — no deterministic mechanism exists for any of these four
formats today, and inventing one would violate the request's own explicit
prohibition on treating inference as fact).

**Why, in the terms the request itself demands (not "impressive/strong/
innovative"):**

- **Technical validity:** the full vision requires a new object model
  (multi-artifact sets) and new I/O class (autonomous file-following) that
  do not exist and are not natural extensions of the current
  single-artifact-per-call architecture — confirmed by exhaustive reads of
  `core/`, all 11 adapters, `cli/`, and `mcp/`.
- **Consistency with existing code:** the narrow version is 100%
  consistent (same `policy: dict` mechanism every other check already
  uses); the full version is not (`core/` would need to learn about
  multiple artifacts for the first time, directly contradicting its own
  stated and, until now, actually-honored "core stays format-agnostic and
  single-artifact" posture).
- **Implementation cost:** narrow ≈ a refactor plus one adapter's worth of
  new policy-key code. Full vision ≈ a new subsystem the size of the
  existing engine, applied to a different object shape, with its own
  security model, test infrastructure, and benchmark category, none of
  which exist today.
- **Maintenance cost:** narrow reduces existing duplication (net
  maintenance win). Full vision adds an entirely new maintenance surface
  (graph consistency, cycle handling, cache invalidation across runs) this
  project has no precedent for maintaining.
- **Security:** narrow makes no change to the current threat model. Full
  vision inverts its core invariant (§16) and would activate two currently-
  latent gaps (PPTX/DOCX/XLSX's missing zip-bomb guard outside EPUB; no
  image decompression-bomb guard) that are otherwise harmless today.
- **Determinism:** narrow has nothing new to be deterministic about. Full
  vision would need to invent node/edge/ID determinism rules from
  scratch, with only a loose analogy (content-hash-as-identity) to build
  from.
- **User value:** real for the narrow version (closes an actual,
  currently-honest `UNKNOWN`/unchecked gap for 6 formats that already
  detect but don't resolve external references). Speculative-to-fabricated
  for the full version across 4 of 11 formats (§20), including, notably,
  Media — the one format category this session's own user context
  (映像・音響・配信 event support) would most want "does this deck's video
  link still work" for, and exactly the format with zero existing
  precedent to build that on safely (§16's `max_video_pixels` finding is a
  direct warning about this format's declared-vs-actual-size risk class).
- **API complexity:** narrow adds nothing new. Full vision adds 5+ new CLI
  verbs, matching MCP tools, and a new evidence artifact — doubling the
  contract surface this project already has to keep CLI/MCP/schema
  consistent (§13/§14), for a capability whose value is unproven outside a
  minority of formats.
- **Risk to existing functionality:** narrow: none. Full vision: real, via
  §16's security gaps and §18's already-open concurrency issue (#47), both
  of which the current single-artifact model has not yet fully closed on
  its own — a strong signal against adding a materially larger subsystem
  on top of infrastructure (`reports/` evidence handling) that isn't fully
  hardened yet.

No part of this recommendation was chosen to please the requester or to
avoid disappointing an interesting-sounding proposal — the evidence
(six real, working implementations of the *narrow* capability already in
production; zero implementations, zero adjacent code, and zero
deterministic mechanism for the *broad* capability in 4 of 11 formats) is
what drove the conclusion, and it points the same direction from every
angle the request asked this review to check.
