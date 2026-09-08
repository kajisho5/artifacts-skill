"""Pytest wrapper around run_benchmark.py's case table (Issue #10) so the
scored benchmark is enforced on every CI run, not just runnable by hand.
Each case gets its own parametrized test (not one big loop asserting
100%) so a regression names exactly which fixture broke.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from cases import (
    EXCEPTION_CASES,
    KNOWN_GOOD_CASES,
    STRUCTURAL_DEFECT_CASES,
    TYPE_DETECTION_CASES,
    TYPE_REJECTED_CASES,
)
from run_benchmark import (
    _check_exception,
    _check_known_good,
    _check_structural_defect,
    _check_type_detection,
    _check_type_rejected,
)


@pytest.mark.parametrize("case", TYPE_REJECTED_CASES, ids=lambda c: c.fixture)
def test_type_rejected(case):
    outcome = _check_type_rejected(case)
    assert outcome.passed, outcome.detail


@pytest.mark.parametrize("case", STRUCTURAL_DEFECT_CASES, ids=lambda c: c.fixture)
def test_structural_defect_detected(case):
    outcome = _check_structural_defect(case)
    assert outcome.passed, outcome.detail


@pytest.mark.parametrize("case", EXCEPTION_CASES, ids=lambda c: c.fixture)
def test_exception_raised(case):
    outcome = _check_exception(case)
    assert outcome.passed, outcome.detail


@pytest.mark.parametrize("case", KNOWN_GOOD_CASES, ids=lambda c: c.fixture)
def test_known_good_verifies_cleanly(case):
    outcome = _check_known_good(case)
    assert outcome.passed, outcome.detail


@pytest.mark.parametrize("case", TYPE_DETECTION_CASES, ids=lambda c: c.fixture)
def test_type_detection_overrides_extension(case):
    outcome = _check_type_detection(case)
    assert outcome.passed, outcome.detail


def test_every_fixture_file_is_covered_by_exactly_one_category_or_known_twice():
    """Guards against the benchmark quietly going stale as fixtures are
    added/removed: every file actually on disk under tests/fixtures/
    must appear in the case table (skipping conftest.py's own directory
    if any), and every case must point at a file that still exists."""
    from cases import FIXTURES_ROOT

    repo_root = Path(__file__).parent.parent.parent
    on_disk: set[str] = set()
    for fmt_dir in sorted((repo_root / FIXTURES_ROOT).iterdir()):
        if not fmt_dir.is_dir() or fmt_dir.name == "__pycache__":
            continue
        for f in fmt_dir.iterdir():
            if f.is_file() and f.suffix != ".pyc":
                on_disk.add(f"{fmt_dir.name}/{f.name}")

    referenced: set[str] = set()
    for case in (*TYPE_REJECTED_CASES, *STRUCTURAL_DEFECT_CASES, *EXCEPTION_CASES, *KNOWN_GOOD_CASES, *TYPE_DETECTION_CASES):
        assert (repo_root / FIXTURES_ROOT / case.fixture).is_file(), f"benchmark case points at a missing file: {case.fixture}"
        referenced.add(case.fixture)

    missing_from_benchmark = on_disk - referenced
    assert not missing_from_benchmark, (
        f"fixture file(s) on disk with no benchmark case covering them: {sorted(missing_from_benchmark)} "
        "— add a case to tests/benchmark/cases.py (or this benchmark silently stops proving anything about them)."
    )
