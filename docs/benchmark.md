# Scored fixture benchmark

Issue #10 (spec §54, §67 "research integrity"): don't claim a detection
rate without a test actually computing it. This benchmark is that test —
runnable by hand for a human-readable report, and enforced on every CI
run as ordinary pytest cases so a regression fails the build with the
exact fixture that broke, not just a stale number in this file.

## Running it

```bash
python3 tests/benchmark/run_benchmark.py   # human-readable scored report
python -m pytest tests/benchmark/ -q       # the same cases, as CI runs them
```

Whatever those commands print *right now* is the real number — this
document deliberately doesn't restate a specific percentage as a
permanent claim, since the corpus and adapters both keep changing. As of
the commit that added this benchmark: 22/22 known-broken fixtures
correctly flagged, 11/11 known-good fixtures verify cleanly with no false
positive, and 7/7 mislabeled-extension fixtures correctly type-detected
by content. Re-run the commands above rather than trusting that this
sentence has kept up.

## What it covers, and why the case table has five categories, not two

`tests/benchmark/cases.py` holds the declarative case table;
`tests/benchmark/run_benchmark.py` executes it. Every expected outcome in
that table was established by actually running the adapter against the
real fixture and reading back what happened — not predicted from reading
adapter source. Doing that surfaced real shape differences the original
two-category plan ("known-broken" vs. "known-good") didn't anticipate:

1. **Type-rejected** — PPTX/DOCX/XLSX are all zip-based OOXML; their
   `corrupt.*` fixture (and HTML's `binary_garbage.html`) no longer
   sniffs as a valid container of that type at all, so
   `ArtifactRef.from_path()` returns `ArtifactType.UNKNOWN` and
   `adapters.registry.get_adapter()` itself raises
   `ARTIFACT_TYPE_UNSUPPORTED` — the format's own `verify_structural()`
   is never reached. This is still "correctly detected as broken," one
   layer earlier than a structural check.
2. **Structural defects** — PDF/SVG/PNG's corrupted fixtures (and every
   format's other deliberately-broken fixtures) *do* survive type
   detection and reach `verify_structural()`, which reports a
   `<fmt>_validity` (or more specific) `WARN`/`FAIL` check.
3. **Exceptions** — `svg/entity_bomb.svg`'s defect *is* a security
   control (`ARTIFACT_XML_ENTITY_DECLARATION_REJECTED`): it raises
   `ArtifactSecurityError` straight out of `verify_structural()` instead
   of returning a `Check`, because that adapter's `verify_structural()`
   only catches `ArtifactInputError` — deliberately, see
   `docs/security.md`.
4. **Known-good** — proves verification doesn't cry wolf on valid input.
   Two entries (`docx/good.docx`, `xlsx/good.xlsx`) are known-good files
   whose *correct* overall status is `UNKNOWN`, not `PASS` — DOCX has no
   structurally-determinable page count, and XLSX never recalculates
   cached formula values, both by explicit, documented design (see
   `docs/verification.md`). Scoring those as a miss would score an
   intentional honesty feature as a bug.
5. **Type detection** — the six `mislabeled_pdf.<fmt>` fixtures (plus the
   inverse `mislabeled_html.pdf`) prove content-based type detection
   overrides a misleading extension. This is a different capability than
   structural-defect detection, so it's tracked as its own count rather
   than folded into "broken" or "good."

`test_every_fixture_file_is_covered_by_exactly_one_category_or_known_twice`
in `tests/benchmark/test_benchmark.py` walks every file actually on disk
under `tests/fixtures/` and fails if one has no matching case — the
table can't quietly go stale as fixtures are added or removed.

## Extending it for a new format or fixture

1. Add the fixture to `tests/fixtures/generate_fixtures.py` and
   regenerate (see `docs/adapters.md`'s "Writing a new adapter"
   checklist).
2. Run the new file through the adapter by hand first — `python3 -c` a
   quick `verify_structural()` call, or use
   `artifact-skill verify <path> --json` — and read back what actually
   happens. Do not guess the expected status from reading the adapter's
   source; this benchmark's entire premise is that expected outcomes are
   established empirically, not asserted on faith.
3. Add a case to the matching list in `tests/benchmark/cases.py` with
   that observed outcome.
4. Re-run `python -m pytest tests/benchmark/ -q` to confirm.
