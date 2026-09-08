"""Spec #51: --dry-run must guarantee no mutation, no output file, and no
subprocess execution. The PDF adapter never calls subprocess at all today,
so the third guarantee is enforced here by making any subprocess call fail
the test outright, rather than trusting "well, nothing calls it" to remain
true as more adapters are added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.core.artifact import sha256_of
from artifact_skill.core.engine import run_lifecycle
from artifact_skill.security import subprocess_exec


def test_dry_run_never_calls_subprocess(good_pdf, tmp_path, monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("subprocess_exec.run() must never be called during --dry-run")

    monkeypatch.setattr(subprocess_exec, "run", _forbidden)

    output_path = tmp_path / "out.pdf"
    result = run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=tmp_path / "reports", dry_run=True,
    )
    assert result.receipt is None


def test_dry_run_never_mutates_or_creates_files(good_pdf, tmp_path):
    before = sha256_of(good_pdf)
    before_dir_contents = set(tmp_path.iterdir())

    output_path = tmp_path / "out.pdf"
    run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=tmp_path / "reports", dry_run=True,
    )

    assert sha256_of(good_pdf) == before
    assert set(tmp_path.iterdir()) == before_dir_contents


def test_dry_run_output_matches_real_plan(good_pdf, tmp_path):
    """The plan a dry-run reports must be the exact plan execute() would
    follow, not an approximation — otherwise dry-run isn't trustworthy."""
    output_path = tmp_path / "out.pdf"
    dry = run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=tmp_path / "reports", dry_run=True,
    )
    real = run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=tmp_path / "reports", dry_run=False,
    )
    assert dry.plan.to_dict() == real.plan.to_dict()
