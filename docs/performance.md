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

- **Not a load/throughput test in the concurrency sense.** Every call
  here is sequential, on one machine. No conclusion about *concurrent*
  calls or behavior under the resource limits `security/limits.py`
  enforces (those are tested for correctness in
  `tests/unit/test_pdf_pages.py` etc., not for cost here).
- **Not a memory profile.** The reported peak RSS is this whole process's
  cumulative high-water mark across every call in the run, not an
  attribution of memory cost to any single operation, and only reflects
  this Python process — LibreOffice/Chromium run as separate OS
  processes with their own memory footprint this script never measures.

## Size scaling, variance, and batch throughput (`run_scaling_benchmark.py`)

The single-shot numbers above answer "how long does one call cost, once,
on a small correctness fixture." They say nothing about noise, how cost
moves as documents get bigger, or the concrete benefit of the one place
this project already batches work (`EpubAdapter.render()`). A second
script closes those three gaps:

```bash
python3 tests/benchmark/run_scaling_benchmark.py
```

It times N repetitions (not one) per (format, operation), against both
the existing small correctness fixture and a synthetic "large" fixture
generated fresh at run time per format (a 60-page PDF, a 50-slide PPTX, a
500-paragraph DOCX, a 5-sheet x 2,000-row XLSX, a 4000x3000px PNG, a
5,000-paragraph HTML page, a 3,000-element SVG, a 50,000-row CSV, a
1,000-section Markdown doc, a 30-spine-document EPUB) — never added to
the tracked fixture corpus, since these exist purely to observe cost vs.
size, not to test correctness. It also runs a direct throughput
comparison: K single-page `render_local_file()` calls (fresh Chromium
launch each) vs. one `render_local_files()` batch call for the same K
pages (one shared launch).

Findings from a real run on this machine, worth knowing before assuming
these adapters scale linearly and uniformly:

1. **Batching is a real, measured ~3-4x win, not just a documented
   claim.** 10 HTML pages: 777ms/page looped (fresh Chromium launch
   each) vs. 210ms/page batched (one shared launch) — a 3.71x speedup.
   This is the concrete number behind `render_local_files()`'s own
   docstring claim; re-run the script to see it on your machine, since
   the exact multiplier depends on launch cost, which varies by host.
2. **XLSX structural work (`inspect`/`verify`) does not scale like the
   other adapters.** A 5-sheet x 2,000-row workbook (50,000 cells) took
   ~1.6-1.8 seconds for `inspect`/`verify`/`execute` — two full orders of
   magnitude past the small fixture's ~8ms, and far past what a
   comparably-scaled DOCX (500 paragraphs, ~75ms) or PPTX (50 slides,
   ~30ms) cost. openpyxl's per-cell object overhead is the likely
   mechanism (this script doesn't profile inside openpyxl to confirm the
   exact cause), but the practical fact stands regardless of cause: a
   caller working with real multi-thousand-row spreadsheets should
   expect structural checks alone to cost low-single-digit seconds, not
   milliseconds.
3. **Chromium's `full_page=True` screenshot cost is driven by rendered
   page *height*, not input file size, and it doesn't just get slow — at
   some height it fails outright.** A 5,000-paragraph HTML file (plain
   text, ~260KB) rendered to a page ~170,000px tall, and capturing that
   full-page screenshot alone took ~23 seconds (isolated directly:
   navigation was 35ms, the screenshot call was the entire cost). A
   1,000-section Markdown document (headings + list items, converted
   through `markdown-it-py` first) rendered to a page ~395,000px tall —
   and at that height, `page.screenshot(full_page=True)` didn't just get
   slower, it failed outright with `Protocol error (Page.captureScreenshot):
   Unable to capture screenshot`, surfaced as
   `ARTIFACT_RENDER_BACKEND_FAILED`. This is a genuine, previously
   undocumented limitation for any HTML/SVG/CSV/Markdown source that
   renders to an unusually tall single page — tracked as
   [Issue #45](https://github.com/kajisho5/artifacts-skill/issues/45)
   since deciding how to handle it (a pre-check with a clearer error? a
   height cap? pagination?) is a design decision, not something to
   silently pick on its own.
4. **The pure-Python structural adapters (PDF/PPTX/DOCX/image) all scale
   roughly as expected** — cost grows with page/slide/paragraph/pixel
   count, but stays in the tens-to-low-hundreds of milliseconds even at
   the synthetic "large" sizes above. Nothing here contradicts the
   single-shot numbers' "structural work is fast" read; XLSX and the
   Chromium screenshot case are the two real exceptions this deeper look
   found.
5. **LibreOffice-backed render can fail entirely in an environment where
   the binary is genuinely present** — this specific run hit
   `ARTIFACT_RENDER_BACKEND_FAILED` ("Error: source file could not be
   loaded") for every PPTX/DOCX/XLSX render call, on a machine where
   `doctor` reports the `soffice` binary `AVAILABLE`. This reconfirms
   the "present binary is not a guarantee of a working conversion"
   caveat documented above and in `docs/adapters.md`'s PPTX section —
   this time in a different sandbox than the one that originally
   surfaced it, which is itself useful evidence that this failure mode
   is a real, recurring environment characteristic, not a one-off fluke.

## What this second script does *not* establish either

- **Fixture size still isn't infinitely representative.** The synthetic
  "large" fixtures above are one deliberate size per format, not a
  sweep across many sizes — they tell you the shape of the cost curve at
  one additional point, not a fitted function. A caller with a
  real 500-page PDF or a 100,000-row spreadsheet should still re-run
  either script against their own representative files.
- **Still no true concurrency.** "Batch vs. loop" here means one shared
  Chromium launch serving K sequential page navigations within a single
  process, not K requests served in parallel.
