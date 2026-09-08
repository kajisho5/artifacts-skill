#!/usr/bin/env python3
"""Measures wall-clock cost for inspect/execute/render across a
representative fixture per adapter — this project's correctness benchmark
(run_benchmark.py) has always been thorough, but real-world usage cost
(how long does a single `execute` actually take, is `render()` practical
for an agent to call per-document at scale) had never been measured at
all before this script existed. Not part of CI (timings vary by machine
and by whether LibreOffice/Chromium are actually installed) — a
developer-run report, same spirit as run_benchmark.py but for cost
instead of correctness.

Usage (from the repo root):
    python3 tests/benchmark/run_perf_benchmark.py
"""

from __future__ import annotations

import resource
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from artifact_skill.adapters.registry import get_adapter  # noqa: E402
from artifact_skill.core.artifact import ArtifactRef  # noqa: E402
from artifact_skill.core.errors import ArtifactError  # noqa: E402

# One representative "good" fixture per format, reusing the same files
# tests/benchmark/cases.py already treats as known-good — not a new
# fixture corpus, just a different lens on the existing one.
_FIXTURES: dict[str, str] = {
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

# A cheap, non-destructive mutating operation per format, for adapters
# that have one at all (HTML/SVG/CSV/Markdown have none by design — see
# docs/adapters.md's "No mutating operations" note on each).
_EXECUTE_OP: dict[str, tuple[str, dict]] = {
    "pdf": ("metadata_set", {"title": "perf-benchmark"}),
    "pptx": ("metadata_set", {"title": "perf-benchmark"}),
    "docx": ("metadata_set", {"title": "perf-benchmark"}),
    "xlsx": ("metadata_set", {"title": "perf-benchmark"}),
    "image": ("resize", {"width": 100, "height": 100}),
    "epub": ("metadata_set", {"title": "perf-benchmark"}),
}


def _fixture_path(rel: str) -> Path:
    return REPO_ROOT / "tests" / "fixtures" / rel


def _peak_rss_mb() -> float:
    # ru_maxrss is KB on Linux, bytes on macOS - this project's CI runs
    # both (see .github/workflows/ci.yml's matrix), so normalize by
    # platform rather than assume one.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1024 if sys.platform != "darwin" else raw / (1024 * 1024)


def _time_call(fn, *args, **kwargs) -> tuple[float, object | None, str | None]:
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - start
        return elapsed, result, None
    except ArtifactError as exc:
        elapsed = time.perf_counter() - start
        return elapsed, None, f"{exc.code}"


def run() -> list[dict]:
    rows: list[dict] = []
    for fmt, rel in _FIXTURES.items():
        ref = ArtifactRef.from_path(_fixture_path(rel))
        adapter = get_adapter(ref.type)
        row: dict = {"format": fmt, "fixture": rel}

        t, _report, err = _time_call(adapter.inspect, ref)
        row["inspect_ms"] = round(t * 1000, 1) if err is None else None
        row["inspect_error"] = err

        t, _result, err = _time_call(adapter.verify_structural, ref, {})
        row["verify_ms"] = round(t * 1000, 1) if err is None else None
        row["verify_error"] = err

        with tempfile.TemporaryDirectory(prefix="artifacts-skill-perfbench-") as tmp:
            out_dir = Path(tmp) / "render"
            t, render_result, err = _time_call(adapter.render, ref, out_dir)
            row["render_ms"] = round(t * 1000, 1) if err is None else None
            row["render_error"] = err
            row["render_files"] = len(render_result.files) if render_result is not None else 0

        if fmt in _EXECUTE_OP:
            op, op_args = _EXECUTE_OP[fmt]
            with tempfile.TemporaryDirectory(prefix="artifacts-skill-perfbench-exec-") as tmp:
                out_path = Path(tmp) / f"out{_fixture_path(rel).suffix}"
                t, _result, err = _time_call(adapter.execute, ref, op, op_args, out_path)
                row["execute_ms"] = round(t * 1000, 1) if err is None else None
                row["execute_error"] = err
        else:
            row["execute_ms"] = None
            row["execute_error"] = "no mutating operation for this format"

        rows.append(row)
    return rows


def main() -> int:
    rows = run()

    print("=" * 100)
    print("Artifact Skill — performance benchmark (wall-clock, single-call, this machine)")
    print("=" * 100)
    header = f"{'format':<10} {'inspect':>10} {'verify':>10} {'execute':>12} {'render':>12} {'pages/files':>12}"
    print(header)
    print("-" * len(header))
    def _fmt(row: dict, key: str, err_key: str) -> str:
        if row[err_key] is not None:
            return f"{row[err_key]}"[:12]
        return f"{row[key]:.1f}ms" if row[key] is not None else "n/a"

    for row in rows:
        print(
            f"{row['format']:<10} {_fmt(row, 'inspect_ms', 'inspect_error'):>10} "
            f"{_fmt(row, 'verify_ms', 'verify_error'):>10} {_fmt(row, 'execute_ms', 'execute_error'):>12} "
            f"{_fmt(row, 'render_ms', 'render_error'):>12} {row['render_files']:>12}"
        )

    print("-" * len(header))
    print(f"Peak RSS this process (cumulative, not per-call): {_peak_rss_mb():.1f} MB")
    print("=" * 100)
    print(
        "\nNote: execute/render figures for pptx/docx/xlsx include LibreOffice headless\n"
        "startup cost (a fresh soffice process per call, no daemon reuse) - this is the\n"
        "dominant cost for those three formats, not the structural read/write itself.\n"
        "'n/a' means the relevant capability (soffice/Chromium/markdown-it-py) is not\n"
        "installed in this environment, not that the operation is unsupported."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
