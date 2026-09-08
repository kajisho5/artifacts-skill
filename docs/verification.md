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
than a warning and must not be hidden behind one. This is why, in the
current PDF adapter, `verify_structural()` always includes a
`font_embedding: UNKNOWN` check (font embedding completeness genuinely
isn't checked yet) — and why that means **every** `artifact-skill verify`
call on a real PDF today aggregates to `UNKNOWN` overall, not `PASS`, even
when every other check passes.

This is intentional, not an oversight to "fix" by dropping the check. It is
the direct, faithful expression of spec §43 ("Unknown is first-class") and
§17 ("PASS/WARN/FAIL ... 絶対に混同しない"). A caller that wants a strict
PASS/FAIL gate on only the checks it cares about should inspect
`checks[].status` by `id`, not rely on the aggregate `status` field, when
some dimensions are known-unimplemented. The CLI reflects this by exit
code (see below) rather than by quietly rounding UNKNOWN up to PASS.

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
