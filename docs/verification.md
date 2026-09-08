# Verification model

## Six states, never collapsed

`core/verification.py` defines `CheckStatus`:

- **PASS** — the requirement was checked and met.
- **WARN** — met, but with something worth flagging (e.g. mixed page sizes
  when the caller didn't explicitly forbid them).
- **FAIL** — checked and not met.
- **UNKNOWN** — it was checked *for*, but the engine could not reach a
  verdict (missing capability, renderer unavailable, adapter doesn't
  implement this particular check yet — e.g. PDF font-embedding
  completeness today).
- **NOT_CHECKED** — nothing ran this check at all (a bug, or a caller who
  explicitly skipped a phase).
- **SKIPPED** — policy says this check does not apply to this operation
  (e.g. structural verification isn't required for an operation whose spec
  says so).

`aggregate()` combines many checks into one status by precedence (worst
first): `FAIL > UNKNOWN > NOT_CHECKED > WARN > PASS > SKIPPED`. An empty
check list aggregates to `NOT_CHECKED`, never to `PASS` — silence is not
success. `SKIPPED` is ranked *after* `PASS`, deliberately: a policy saying
"this check doesn't apply here" is an intentional, benign absence, not a
process gap — it must not drag a clean result down the way an
*unintentional* gap (`NOT_CHECKED`) does. `NOT_CHECKED` ranks worse than
`WARN` for the opposite reason: "nothing ran this" is a bigger concern than
"it ran and passed with a caveat."

## Why UNKNOWN outranks WARN

A `WARN` means "we looked, and it's probably fine, but note this." An
`UNKNOWN` means "we don't actually know" — that's a strictly weaker claim
than a warning and must not be hidden behind one.

The PDF adapter's `font_embedding` check used to be the canonical example
of this: for a long stretch of this project it was unconditionally
`UNKNOWN`, and every real PDF's `verify` rolled up to `UNKNOWN` overall as
a result — not because anything was wrong, but because embedding
completeness genuinely wasn't checked yet. That's now implemented (Issue
#7; see `docs/adapters.md`'s PDF section): fonts are inspected via
`pypdf`'s object model, standard-14 fonts are exempt (every conformant
viewer renders them correctly unembedded), and a non-embedded custom font
is `WARN` by default or `FAIL` under `policy.forbid_unembedded_fonts`. A
plain, standard-font PDF genuinely rolls up to `PASS` now.

The pattern that check demonstrated is still very much alive elsewhere,
because it's a real, recurring shape, not a one-off gap:
- PPTX's `chart_validity` is `UNKNOWN` on any deck containing a chart
  (presence is detected, internal chart data correctness is not) and
  `SKIPPED` — not `PASS` — on a chart-free deck, so a chart-free deck's
  status is unaffected while a chart-bearing one honestly reflects the gap.
- DOCX's `page_count` is `UNKNOWN` unconditionally, because the format
  itself has no fixed pagination in its XML — this one isn't a "not
  implemented yet" gap like font embedding was; it's structurally
  unanswerable without actually rendering the document.
- XLSX's `formula_recalculation` is `UNKNOWN` whenever any formula is
  present, unconditionally, by deliberate design choice (see Issue #5 /
  `docs/adapters.md`'s XLSX section) — recalculating would mean shelling
  out to a LibreOffice macro interface for a speculative benefit, so this
  adapter reports the honest gap instead of closing it that way.

None of these are oversights to "fix" by dropping the check. They are the
direct, faithful expression of spec §43 ("Unknown is first-class") and
§17 ("PASS/WARN/FAIL ... 絶対に混同しない"). A caller that wants a strict
PASS/FAIL gate on only the checks it cares about should inspect
`checks[].status` by `id`, not rely on the aggregate `status` field, when
some dimensions are known-unimplemented or structurally unanswerable. The
CLI reflects this by exit code (see below) rather than by quietly
rounding UNKNOWN up to PASS.

## Exit codes for verification status

`cli/main.py`'s `_exit_for_status()`:

| Status | Exit code | Rationale |
|---|---|---|
| PASS, WARN, SKIPPED, NOT_CHECKED | 0 | Nothing is actionably wrong |
| FAIL | 1 | An actual, checked failure |
| UNKNOWN | 3 (same bucket as `ARTIFACT_CAPABILITY_MISSING`) | Distinct from both success and failure — something couldn't be verified |

This lets a CI script distinguish "verification genuinely failed" (1) from
"verification was incomplete" (3) without parsing JSON.

## Structural vs. visual

- **Structural verification** (`ArtifactAdapter.verify_structural`) is
  always automated and adapter-specific: page count, page size, encryption,
  embedded JavaScript, extractable text, metadata match, and whatever a
  caller passes in `policy`.
- **Visual verification** is split into two parts by design (see
  `docs/architecture.md` "Brain/Hands split"):
  1. The engine renders pages and reports a `visual_evidence` check —
     `PASS` if images were produced, `WARN` if rendering produced zero
     pages, `UNKNOWN`/`SKIPPED` if the render capability is
     missing/not required. This is a claim about *evidence existing*, not
     about the artifact looking correct.
  2. `inspected_by` stays `None` until an agent (or human) that actually
     looked at the rendered images records a verdict. Nothing in this
     codebase sets `inspected_by="agent"` on its own — doing so would be
     exactly the "hallucinated success" spec §17/§42 forbids.

## Policy-driven checks (PDF adapter)

Pass a `policy` object to `verify`/`receipt`:

```json
{
  "require_page_count": 5,
  "min_pages": 1,
  "max_pages": 20,
  "require_page_size_pt": [612, 792],
  "page_size_tolerance_pt": 1.0,
  "allow_mixed_page_sizes": false,
  "require_no_encryption": true,
  "forbid_javascript": true,
  "require_metadata": { "Title": "Q3 Report" }
}
```

Unset keys simply don't add their corresponding check — they don't default
to strict or lenient in a way that changes other checks' behavior.

## Receipt-level status

`ProductionReceipt.status` (see `receipt/model.py`) aggregates: any failed
`OperationRecord`, the structural `VerificationResult.status`, and the
visual `VerificationResult.status`. A receipt whose operation succeeded and
whose structural checks all passed can still show overall `UNKNOWN` if
visual evidence couldn't be produced (missing render capability) or if a
structural check is itself `UNKNOWN` (as font-embedding is today) — this is
the same "don't hide the unknown" rule applied one level up.
