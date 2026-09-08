#!/usr/bin/env python3
"""Runs the scored fixture benchmark (Issue #10, spec §54/§67) and prints a
real, computed detection-rate report — never an estimated one.

Usage (from the repo root):
    python3 tests/benchmark/run_benchmark.py

Exit code 0 if every case matched its expected outcome, 1 otherwise (so
this doubles as a CI-friendly check independent of pytest).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from cases import (  # noqa: E402
    EXCEPTION_CASES,
    FIXTURES_ROOT,
    KNOWN_GOOD_CASES,
    STRUCTURAL_DEFECT_CASES,
    TYPE_DETECTION_CASES,
    TYPE_REJECTED_CASES,
)

from artifact_skill.adapters.registry import get_adapter  # noqa: E402
from artifact_skill.core.artifact import ArtifactRef  # noqa: E402
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactError  # noqa: E402


def _fixture_path(fixture: str) -> Path:
    return REPO_ROOT / FIXTURES_ROOT / fixture


class Outcome:
    def __init__(self, case_desc: str, passed: bool, detail: str) -> None:
        self.case_desc = case_desc
        self.passed = passed
        self.detail = detail


def _check_type_rejected(case) -> Outcome:
    ref = ArtifactRef.from_path(_fixture_path(case.fixture))
    try:
        get_adapter(ref.type)
        return Outcome(case.fixture, False, f"expected ARTIFACT_TYPE_UNSUPPORTED, got a working adapter for type={ref.type.value}")
    except ArtifactCapabilityError as exc:
        ok = exc.code == "ARTIFACT_TYPE_UNSUPPORTED"
        return Outcome(case.fixture, ok, f"type={ref.type.value}, code={exc.code}")


def _check_structural_defect(case) -> Outcome:
    ref = ArtifactRef.from_path(_fixture_path(case.fixture))
    adapter = get_adapter(ref.type)
    try:
        result = adapter.verify_structural(ref, case.policy)
    except ArtifactError as exc:
        return Outcome(case.fixture, False, f"expected status={case.expected_status.value}, but verify_structural() raised {exc.code}")
    ok = result.status == case.expected_status
    return Outcome(case.fixture, ok, f"expected={case.expected_status.value}, actual={result.status.value}")


def _check_exception(case) -> Outcome:
    ref = ArtifactRef.from_path(_fixture_path(case.fixture))
    adapter = get_adapter(ref.type)
    try:
        adapter.verify_structural(ref, {})
        return Outcome(case.fixture, False, f"expected {case.expected_exception_code} to be raised, but verify_structural() returned normally")
    except ArtifactError as exc:
        ok = exc.code == case.expected_exception_code
        return Outcome(case.fixture, ok, f"expected={case.expected_exception_code}, actual={exc.code}")


def _check_known_good(case) -> Outcome:
    ref = ArtifactRef.from_path(_fixture_path(case.fixture))
    adapter = get_adapter(ref.type)
    result = adapter.verify_structural(ref, case.policy)
    ok = result.status == case.expected_status
    return Outcome(case.fixture, ok, f"expected={case.expected_status.value}, actual={result.status.value}")


def _check_type_detection(case) -> Outcome:
    ref = ArtifactRef.from_path(_fixture_path(case.fixture))
    ok = ref.type == case.expected_type
    return Outcome(case.fixture, ok, f"expected={case.expected_type.value}, actual={ref.type.value}")


def run() -> tuple[list[Outcome], list[Outcome], list[Outcome], list[Outcome], list[Outcome]]:
    type_rejected = [_check_type_rejected(c) for c in TYPE_REJECTED_CASES]
    structural_defect = [_check_structural_defect(c) for c in STRUCTURAL_DEFECT_CASES]
    exceptions = [_check_exception(c) for c in EXCEPTION_CASES]
    known_good = [_check_known_good(c) for c in KNOWN_GOOD_CASES]
    type_detection = [_check_type_detection(c) for c in TYPE_DETECTION_CASES]
    return type_rejected, structural_defect, exceptions, known_good, type_detection


def _report_group(title: str, outcomes: list[Outcome]) -> bool:
    passed = sum(1 for o in outcomes if o.passed)
    total = len(outcomes)
    print(f"\n{title}: {passed}/{total}")
    for o in outcomes:
        mark = "OK  " if o.passed else "MISS"
        print(f"  [{mark}] {o.case_desc} — {o.detail}")
    return passed == total


def main() -> int:
    type_rejected, structural_defect, exceptions, known_good, type_detection = run()

    print("=" * 72)
    print("Artifact Skill — scored fixture benchmark")
    print("=" * 72)

    all_ok = True
    all_ok &= _report_group("Type-rejected (garbage content correctly refused)", type_rejected)
    all_ok &= _report_group("Structural defects correctly flagged (WARN/FAIL)", structural_defect)
    all_ok &= _report_group("Security-control exceptions correctly raised", exceptions)
    all_ok &= _report_group("Known-good fixtures verify cleanly (no false positives)", known_good)
    all_ok &= _report_group("Type detection overrides misleading extensions", type_detection)

    detection_cases = type_rejected + structural_defect + exceptions
    detection_passed = sum(1 for o in detection_cases if o.passed)
    detection_total = len(detection_cases)
    false_positive_cases = known_good
    false_positive_passed = sum(1 for o in false_positive_cases if o.passed)

    print("\n" + "=" * 72)
    print(
        f"Known-broken detection rate: {detection_passed}/{detection_total} "
        f"({100 * detection_passed / detection_total:.0f}%)"
    )
    print(
        f"Known-good clean-verify rate: {false_positive_passed}/{len(false_positive_cases)} "
        f"({100 * false_positive_passed / len(false_positive_cases):.0f}%)"
    )
    print(
        f"Type-detection-by-content accuracy: "
        f"{sum(1 for o in type_detection if o.passed)}/{len(type_detection)}"
    )
    print("=" * 72)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
