from __future__ import annotations

from pathlib import Path

from artifact_skill.core.engine import run_lifecycle
from artifact_skill.core.verification import CheckStatus


def test_dry_run_performs_no_io(good_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=evidence_dir, dry_run=True,
    )

    assert result.receipt is None
    assert result.output is None
    assert not output_path.exists()
    assert not evidence_dir.exists()
    assert result.plan.operation == "pdf.metadata_set"


def test_real_run_produces_receipt_and_evidence(good_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        good_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=evidence_dir, dry_run=False,
    )

    assert result.receipt is not None
    assert output_path.exists()
    assert (evidence_dir / "receipt.json").exists()
    assert result.receipt.operations[0]["succeeded"] is True
    # visual evidence: pdf.render is available in this dev environment
    assert len(result.receipt.artifacts) == 2


def test_fix_loop_stops_at_one_iteration_when_no_fixer_registered(empty_pdf, tmp_path):
    """The PDF adapter has no fixer (adapters/base.py's default `fix()`
    returns None). A structural FAIL (empty_pdf has 0 pages) must not
    spin `max_iterations` times pointlessly — it should record exactly
    one iteration and a limitation explaining why it stopped."""
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        empty_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=evidence_dir, dry_run=False, max_iterations=3,
    )

    assert result.receipt is not None
    assert result.receipt.iterations == 1
    assert any("no automatic fixer" in lim for lim in result.receipt.limitations)
    assert result.receipt.verification["structural"]["status"] == CheckStatus.FAIL.value


def test_receipt_status_reflects_structural_failure(empty_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"
    result = run_lifecycle(
        empty_pdf, "metadata_set", {"title": "x"}, output_path,
        evidence_dir=evidence_dir, dry_run=False,
    )
    assert result.receipt.status == CheckStatus.FAIL
