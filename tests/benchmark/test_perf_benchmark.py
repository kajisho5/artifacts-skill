"""Sanity guard for run_perf_benchmark.py (docs/performance.md) - asserts
the script still runs end-to-end and produces a well-shaped row per
format, never a timing threshold (this is a cost report, not a
correctness gate; see docs/performance.md for why it's not wired into
CI's pass/fail path the way tests/benchmark/test_benchmark.py is).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_perf_benchmark import _FIXTURES, run


def test_perf_benchmark_runs_and_covers_every_format():
    rows = run()
    formats = {row["format"] for row in rows}
    assert formats == set(_FIXTURES)


def test_perf_benchmark_inspect_and_verify_always_succeed_on_known_good_fixtures():
    """inspect()/verify_structural() have no external-binary dependency
    for any adapter (unlike execute()/render(), which can legitimately be
    unavailable in a given environment) - these must never fail on the
    project's own known-good fixtures."""
    rows = run()
    for row in rows:
        assert row["inspect_error"] is None, f"{row['format']}: inspect failed: {row['inspect_error']}"
        assert row["verify_error"] is None, f"{row['format']}: verify failed: {row['verify_error']}"
        assert row["inspect_ms"] is not None and row["inspect_ms"] >= 0
        assert row["verify_ms"] is not None and row["verify_ms"] >= 0
