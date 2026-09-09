#!/usr/bin/env python3
"""Broader performance benchmark, complementing `run_perf_benchmark.py`.

`run_perf_benchmark.py` answers "how long does one call cost, once, on a
small fixture" — useful, but `docs/performance.md`'s own "What this does
not establish" section names exactly what it leaves open: single-shot
timing has no noise/variance information, every fixture is deliberately
tiny (correctness fixtures, not representative documents), and there is
no throughput comparison for the one place this project already does
batch work (`EpubAdapter.render()` via `render_local_files()`).

This script closes those three gaps instead of re-measuring the same
single small-fixture numbers:

1. **Variance**: N repetitions per (format, operation), reporting
   min/median/max instead of one point-in-time number.
2. **Size scaling**: a synthetic "large" fixture per format (generated
   into a temp dir at run time, not added to the tracked fixture corpus —
   these exist purely to see how cost moves with size, not to test
   correctness), timed the same way as the existing small fixture.
3. **Batch vs. per-call throughput**: K single-page `render_local_file()`
   calls (one fresh Chromium launch each) vs. one `render_local_files()`
   batch call for K pages (one shared launch) — the concrete numbers
   behind `render_local_files()`'s own docstring claim that launch
   overhead, not page rendering, dominates repeated single-file calls.

Not part of CI, same as `run_perf_benchmark.py` — timings vary by
machine and by whether LibreOffice/Chromium are actually installed and
working. A developer-run report, not a pass/fail gate.

Usage (from the repo root):
    python3 tests/benchmark/run_scaling_benchmark.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from artifact_skill.adapters.registry import get_adapter  # noqa: E402
from artifact_skill.core.artifact import ArtifactRef  # noqa: E402
from artifact_skill.core.errors import ArtifactError  # noqa: E402
from artifact_skill.rendering.chromium_render import render_local_file, render_local_files  # noqa: E402

_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

# ---- repetition counts -----------------------------------------------
# Cheap, pure-Python operations get more reps (noise matters more relative
# to their own tiny duration); subprocess-launching operations (soffice,
# Chromium) get fewer, since each rep costs real wall-clock seconds and
# their variance is dominated by launch cost either way.
_REPS_CHEAP = 7
_REPS_EXPENSIVE = 3


def _timed(fn, *args, reps: int, **kwargs) -> tuple[list[float], str | None]:
    """Runs `fn` `reps` times, returning (durations_ms, first_error_code).
    Stops after the first error (no point repeating a broken call) but
    still returns whatever timings were collected before it."""
    durations: list[float] = []
    for _ in range(reps):
        start = time.perf_counter()
        try:
            fn(*args, **kwargs)
        except ArtifactError as exc:
            return durations, exc.code
        durations.append((time.perf_counter() - start) * 1000)
    return durations, None


def _stats(durations: list[float]) -> str:
    if not durations:
        return "n/a"
    if len(durations) == 1:
        return f"{durations[0]:.1f}ms (1 rep)"
    return (
        f"{statistics.median(durations):.1f}ms median "
        f"[{min(durations):.1f}-{max(durations):.1f}]"
    )


# ---- synthetic "large" fixture generators -----------------------------
# Each returns the path to a freshly-built large fixture inside `tmp_dir`,
# and a short note on what "large" means for that format (page/row/element
# count) - printed alongside the timing so the numbers are read against a
# concrete size, not a mystery file.


def _large_pdf(tmp_dir: Path) -> tuple[Path, str]:
    import pypdf

    writer = pypdf.PdfWriter()
    for _ in range(60):
        writer.add_blank_page(width=595, height=842)
    path = tmp_dir / "large.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path, "60 pages"


def _large_pptx(tmp_dir: Path) -> tuple[Path, str]:
    import pptx

    prs = pptx.Presentation()
    layout = prs.slide_layouts[6]  # blank
    for i in range(50):
        slide = prs.slides.add_slide(layout)
        tb = slide.shapes.add_textbox(0, 0, prs.slide_width, prs.slide_height // 4)
        tb.text_frame.text = f"Slide {i + 1} — benchmark content " * 5
    path = tmp_dir / "large.pptx"
    prs.save(str(path))
    return path, "50 slides"


def _large_docx(tmp_dir: Path) -> tuple[Path, str]:
    import docx

    document = docx.Document()
    for i in range(500):
        document.add_paragraph(f"Paragraph {i + 1}: benchmark content. " * 8)
    path = tmp_dir / "large.docx"
    document.save(str(path))
    return path, "500 paragraphs"


def _large_xlsx(tmp_dir: Path) -> tuple[Path, str]:
    import openpyxl

    wb = openpyxl.Workbook()
    for sheet_i in range(5):
        ws = wb.create_sheet(f"Sheet{sheet_i}") if sheet_i > 0 else wb.active
        ws.title = f"Sheet{sheet_i}"
        for row in range(1, 2001):
            ws.append([row, row * 2, f"row-{row}", row * 1.5, f"note {row}"])
    path = tmp_dir / "large.xlsx"
    wb.save(str(path))
    return path, "5 sheets x 2000 rows"


def _large_image(tmp_dir: Path) -> tuple[Path, str]:
    from PIL import Image

    img = Image.new("RGB", (4000, 3000), color=(120, 140, 160))
    path = tmp_dir / "large.png"
    img.save(path)
    return path, "4000x3000px"


def _large_html(tmp_dir: Path) -> tuple[Path, str]:
    body = "".join(f"<p>Paragraph {i} — benchmark filler content.</p>\n" for i in range(5000))
    path = tmp_dir / "large.html"
    path.write_text(f"<!DOCTYPE html><html><head><title>Large</title></head><body>{body}</body></html>")
    return path, "5000 paragraphs"


def _large_svg(tmp_dir: Path) -> tuple[Path, str]:
    shapes = "".join(
        f'<rect x="{i % 100 * 10}" y="{i // 100 * 10}" width="8" height="8" fill="#{i % 999:03x}"/>\n'
        for i in range(3000)
    )
    path = tmp_dir / "large.svg"
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000">{shapes}</svg>')
    return path, "3000 rect elements"


def _large_csv(tmp_dir: Path) -> tuple[Path, str]:
    lines = ["id,name,value,note"]
    lines.extend(f"{i},item-{i},{i * 1.5},note text {i}" for i in range(50_000))
    path = tmp_dir / "large.csv"
    path.write_text("\n".join(lines))
    return path, "50,000 rows"


def _large_markdown(tmp_dir: Path) -> tuple[Path, str]:
    # Headings alone are only one Markdown signal (core/artifact.py's
    # _looks_like_markdown() deliberately requires two distinct signal
    # types, or one unambiguous one, so a single "# comment"-shaped line
    # isn't mistaken for a shell/Python comment) - a list item line adds
    # the second signal, matching what a realistic Markdown document
    # actually looks like anyway.
    parts = [f"## Section {i}\n\n- point one\n- point two\n\nParagraph text for section {i}. " * 4 + "\n" for i in range(1000)]
    path = tmp_dir / "large.md"
    path.write_text("# Large document\n\n" + "\n".join(parts))
    return path, "1000 sections"


def _large_epub(tmp_dir: Path) -> tuple[Path, str]:
    n_docs = 30
    container_xml = (
        b'<?xml version="1.0"?>'
        b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b'<rootfiles><rootfile full-path="OEBPS/content.opf" '
        b'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    items = "".join(
        f'<item id="ch{i}" href="text/ch{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(n_docs)
    )
    spine = "".join(f'<itemref idref="ch{i}"/>' for i in range(n_docs))
    opf = (
        b'<?xml version="1.0"?>'
        b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Large</dc:title></metadata>'
        + f"<manifest>{items}</manifest><spine>{spine}</spine></package>".encode()
    )
    path = tmp_dir / "large.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf)
        for i in range(n_docs):
            zf.writestr(
                f"OEBPS/text/ch{i}.xhtml",
                f"<html><body><h1>Chapter {i}</h1><p>Content for chapter {i}.</p></body></html>".encode(),
            )
    return path, f"{n_docs} spine documents"


_SMALL_FIXTURES: dict[str, str] = {
    "pdf": "pdf/good_2page.pdf",
    "pptx": "pptx/good_2slide.pptx",
    "docx": "docx/good.docx",
    "xlsx": "xlsx/good.xlsx",
    "image": "image/good.png",
    "html": "html/good.html",
    "svg": "svg/good.svg",
    "csv": "csv/good.csv",
    "markdown": "markdown/good.md",
    "epub": "epub/good.epub",
}

_LARGE_BUILDERS = {
    "pdf": _large_pdf,
    "pptx": _large_pptx,
    "docx": _large_docx,
    "xlsx": _large_xlsx,
    "image": _large_image,
    "html": _large_html,
    "svg": _large_svg,
    "csv": _large_csv,
    "markdown": _large_markdown,
    "epub": _large_epub,
}

_EXECUTE_OP: dict[str, tuple[str, dict]] = {
    "pdf": ("metadata_set", {"title": "scaling-benchmark"}),
    "pptx": ("metadata_set", {"title": "scaling-benchmark"}),
    "docx": ("metadata_set", {"title": "scaling-benchmark"}),
    "xlsx": ("metadata_set", {"title": "scaling-benchmark"}),
    "image": ("resize", {"width": 200, "height": 200}),
    "epub": ("metadata_set", {"title": "scaling-benchmark"}),
}

# Formats whose render()/execute() launches a subprocess (soffice or
# Chromium) - these get fewer reps (_REPS_EXPENSIVE) than pure-Python
# structural work, which is cheap enough to repeat more (_REPS_CHEAP).
_SUBPROCESS_RENDER = {"pptx", "docx", "xlsx", "html", "svg", "csv", "markdown", "epub"}


def _bench_one(fmt: str, path: Path, size_label: str) -> dict:
    ref = ArtifactRef.from_path(path)
    adapter = get_adapter(ref.type)
    row: dict = {"format": fmt, "size": size_label}

    durations, err = _timed(adapter.inspect, ref, reps=_REPS_CHEAP)
    row["inspect"] = _stats(durations) if err is None else f"ERROR {err}"

    durations, err = _timed(adapter.verify_structural, ref, {}, reps=_REPS_CHEAP)
    row["verify"] = _stats(durations) if err is None else f"ERROR {err}"

    reps = _REPS_EXPENSIVE if fmt in _SUBPROCESS_RENDER else _REPS_CHEAP
    with tempfile.TemporaryDirectory(prefix="artifacts-skill-scalebench-render-") as tmp:

        def _render_call():
            adapter.render(ref, Path(tmp) / f"render-{time.time_ns()}")

        durations, err = _timed(_render_call, reps=reps)
        row["render"] = _stats(durations) if err is None else f"ERROR {err}"

    if fmt in _EXECUTE_OP:
        op, op_args = _EXECUTE_OP[fmt]
        reps = _REPS_EXPENSIVE if fmt in _SUBPROCESS_RENDER else _REPS_CHEAP
        with tempfile.TemporaryDirectory(prefix="artifacts-skill-scalebench-exec-") as tmp:

            def _execute_call():
                adapter.execute(ref, op, op_args, Path(tmp) / f"out-{time.time_ns()}{path.suffix}")

            durations, err = _timed(_execute_call, reps=reps)
            row["execute"] = _stats(durations) if err is None else f"ERROR {err}"
    else:
        row["execute"] = "(no mutating op)"

    return row


def _failed_row(fmt: str, size: str) -> dict:
    return {"format": fmt, "size": size, "inspect": "n/a", "verify": "n/a", "render": "n/a", "execute": "n/a"}


def run_size_scaling() -> list[dict]:
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="artifacts-skill-scalebench-fixtures-") as tmp_str:
        tmp_dir = Path(tmp_str)
        for fmt, rel in _SMALL_FIXTURES.items():
            small_path = _FIXTURES_DIR / rel
            try:
                rows.append(_bench_one(fmt, small_path, "small"))
            except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
                rows.append(_failed_row(fmt, f"small (failed: {exc})"))
            try:
                large_path, size_label = _LARGE_BUILDERS[fmt](tmp_dir)
                rows.append(_bench_one(fmt, large_path, f"large ({size_label})"))
            except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
                rows.append(_failed_row(fmt, f"large (failed: {exc})"))
    return rows


def run_batch_vs_loop_throughput(n_pages: int = 10) -> dict:
    """The concrete numbers behind render_local_files()'s docstring claim:
    K single-page render_local_file() calls (fresh Chromium launch each)
    vs. one render_local_files() batch call for the same K pages (one
    shared launch)."""
    with tempfile.TemporaryDirectory(prefix="artifacts-skill-scalebench-batch-") as tmp_str:
        tmp_dir = Path(tmp_str)
        source_paths = []
        out_paths_loop = []
        out_paths_batch = []
        for i in range(n_pages):
            src = tmp_dir / f"page{i}.html"
            src.write_text(f"<html><body><h1>Page {i}</h1></body></html>")
            source_paths.append(src)
            out_paths_loop.append(tmp_dir / f"loop-out{i}.png")
            out_paths_batch.append(tmp_dir / f"batch-out{i}.png")

        start = time.perf_counter()
        for src, out in zip(source_paths, out_paths_loop, strict=True):
            render_local_file(src, out, capability_id="html.render")
        loop_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        render_local_files(source_paths, out_paths_batch, capability_id="html.render")
        batch_ms = (time.perf_counter() - start) * 1000

    return {
        "n_pages": n_pages,
        "loop_total_ms": round(loop_ms, 1),
        "loop_per_page_ms": round(loop_ms / n_pages, 1),
        "batch_total_ms": round(batch_ms, 1),
        "batch_per_page_ms": round(batch_ms / n_pages, 1),
        "speedup": round(loop_ms / batch_ms, 2) if batch_ms > 0 else None,
    }


def main() -> int:
    print("=" * 100)
    print("Artifact Skill — scaling & throughput benchmark (this machine, developer-run)")
    print("=" * 100)

    print("\n--- Size scaling: small (correctness fixture) vs. synthetic large ---\n")
    rows = run_size_scaling()
    header = f"{'format':<10} {'size':<28} {'inspect':<26} {'verify':<26} {'execute':<26} {'render':<26}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['format']:<10} {row['size']:<28} {row.get('inspect', 'n/a'):<26} "
            f"{row.get('verify', 'n/a'):<26} {row.get('execute', 'n/a'):<26} {row.get('render', 'n/a'):<26}"
        )

    print("\n--- Batch vs. per-call render throughput (HTML, Chromium-backed) ---\n")
    try:
        result = run_batch_vs_loop_throughput()
        print(f"Pages: {result['n_pages']}")
        print(f"  Loop  (fresh launch per page):  {result['loop_total_ms']:.1f}ms total, "
              f"{result['loop_per_page_ms']:.1f}ms/page")
        print(f"  Batch (one shared launch):      {result['batch_total_ms']:.1f}ms total, "
              f"{result['batch_per_page_ms']:.1f}ms/page")
        if result["speedup"] is not None:
            print(f"  Speedup from batching: {result['speedup']:.2f}x")
    except ArtifactError as exc:
        print(f"  Skipped: {exc.code} ({exc.message})")

    print("\n" + "=" * 100)
    print(
        "Notes:\n"
        "- 'large' fixtures are generated fresh each run (not tracked fixtures) - see each\n"
        "  _large_* builder above for exactly what was generated.\n"
        "- execute/render reps are lower for subprocess-launching formats (soffice/Chromium)\n"
        "  since each rep costs real wall-clock seconds; structural work (inspect/verify) gets\n"
        "  more reps since it's cheap enough that noise matters more relative to duration.\n"
        "- This is still one machine, sequential calls, no concurrency - see docs/performance.md\n"
        "  for what neither this script nor run_perf_benchmark.py establishes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
