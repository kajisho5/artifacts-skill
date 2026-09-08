# Performance / resource cost

Self-audit finding (a critical review from a "how would this hold up in
real agent usage" angle, distinct from the correctness-focused audits
`docs/benchmark.md` covers): every prior round of testing measured
whether an operation produced the *right* result, never how long it took
or what it cost to run. An agent calling `execute`/`render` per document,
possibly at volume, has no documented basis for estimating that cost. This
document exists to close that gap — not with a permanent number (this
project's own convention per `docs/benchmark.md`: numbers drift, re-run
the script), but with a runnable measurement and an honest read of what
dominates the cost.

## Running it

```bash
python3 tests/benchmark/run_perf_benchmark.py
```

Not part of CI and not a pass/fail gate — timings vary by machine, and by
whether LibreOffice/Chromium are actually installed and working in a given
environment (see below). It's a developer-run report, the performance
counterpart to `tests/benchmark/run_benchmark.py`'s correctness report.

## What it measures

One representative "good" fixture per format (the same files
`tests/benchmark/cases.py` already treats as known-good — not a separate
corpus), timing a single call to `inspect()`, `verify_structural()`,
`execute()` (for formats with a mutating operation), and `render()`. Wall
clock only (`time.perf_counter()`), plus this process's own cumulative
peak RSS (`resource.getrusage().ru_maxrss`) as a rough, whole-run memory
signal — not a per-call breakdown, since Python's own `resource` module
doesn't offer one without a heavier profiler this project has no other
reason to depend on.

## What the numbers actually show (read the shape, not the milliseconds)

A representative run on a modest CI-class Linux container:

```
format        inspect     verify      execute       render  pages/files
-----------------------------------------------------------------------
pdf            49.7ms      2.5ms        4.1ms       89.6ms            2
pptx           58.4ms      4.3ms       11.0ms          n/a            0
docx           52.5ms      9.4ms       24.7ms          n/a            0
xlsx           61.7ms      6.1ms       12.8ms          n/a            0
image           0.6ms      0.4ms        2.6ms        6.5ms            1
html            0.4ms      0.2ms   (no op)       577.1ms            1
svg             0.1ms      0.1ms   (no op)       512.1ms            1
csv             0.7ms      0.2ms   (no op)       544.1ms            1
markdown        0.2ms      0.1ms   (no op)       572.8ms            1
epub            0.3ms      0.2ms        2.4ms          n/a            0
```

(`n/a` for pptx/docx/xlsx in that specific run reflects this environment's
LibreOffice install failing to convert — see the caveat below, not a code
defect; `n/a` for epub is the documented `NOT_IMPLEMENTED` render gap,
see `docs/adapters.md`'s EPUB section.)

Three real patterns, not just numbers to memorize:

1. **Structural work (`inspect`/`verify`) is fast across every format** —
   sub-millisecond for the pure-stdlib adapters (image/html/svg/csv/
   markdown/epub), tens of milliseconds for the `python-pptx`/`python-
   docx`/`openpyxl` adapters (parsing a real OOXML zip has real, if
   modest, cost). None of this is the bottleneck.
2. **Chromium-backed `render()` (html/svg/csv/markdown) costs ~500ms per
   call, dominated by browser launch** — Playwright starts a fresh
   Chromium process per `render()` call, no persistent browser reused
   across calls. For a single document this is a non-issue; for an agent
   rendering many documents in a loop, launch overhead — not the actual
   page render — is what adds up. There is no browser-reuse path today
   (each `render()` is independently correct and isolated, which is also
   why there's no cross-call state to worry about) — a legitimate future
   optimization if bulk rendering becomes a real workload, not something
   this audit round implemented speculatively.
3. **LibreOffice-backed `execute()`/`render()` (pptx/docx/xlsx) is the
   most expensive path when it works, and this project already documents
   exhaustively why "when it works" isn't guaranteed** — a fresh `soffice`
   headless process is launched per call (see
   `rendering/office_convert.py`'s module docstring), and a present
   `soffice` binary on PATH is not a guarantee a given environment's
   install can actually convert a specific document (the exact failure
   this benchmark run hit — `doctor` reports `pptx.render`/`docx.render`/
   `xlsx.render` as `AVAILABLE`, meaning the binary was found, but the
   real conversion still failed at call time with
   `ARTIFACT_RENDER_BACKEND_FAILED`, precisely the honest-failure-not-
   silent-crash behavior those adapters' docstrings describe). This is an
   environment characteristic (this sandbox's LibreOffice install, not
   this project's code), reported here rather than papered over, since a
   caller estimating real-world cost needs to know this failure mode
   exists and is not rare.

## What this does *not* establish

- **Not a load/throughput test.** One call per format, sequentially, on
  one machine. No conclusion about concurrent calls, sustained
  throughput, or behavior under the resource limits `security/limits.py`
  enforces (those are tested for correctness in
  `tests/unit/test_pdf_pages.py` etc., not for cost here).
- **Not a memory profile.** The reported peak RSS is this whole process's
  cumulative high-water mark across every call in the run, not an
  attribution of memory cost to any single operation.
- **Fixture size matters and these fixtures are small** — `good_2page.pdf`
  et al. are deliberately minimal known-good fixtures for correctness
  testing (`docs/benchmark.md`), not representative of a real 50-page
  deck or a multi-MB spreadsheet. A caller with large real documents
  should re-run this script against their own representative files rather
  than trust these numbers to scale linearly (LibreOffice/Chromium launch
  overhead is roughly fixed per call regardless of document size, but
  structural parsing and page rendering are not).
