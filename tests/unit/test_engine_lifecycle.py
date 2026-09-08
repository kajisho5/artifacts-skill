from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.core.engine import build_plan, run_lifecycle
from artifact_skill.core.errors import ArtifactInputError
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


def test_fix_loop_actually_fixes_page_size_mismatch(good_pdf, tmp_path):
    """The one real fixer registered today (PdfAdapter.fix() for
    fit_page_size, Issue #8): iteration 1 scales to the wrong target
    (a plausible caller mistake), the required-page-size policy fails
    structural verify, the fixer reads the expected size straight off
    that failure's evidence, and iteration 2 succeeds with it — proving
    the fix-loop architecture works end-to-end, not just in the abstract.
    """
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        good_pdf, "fit_page_size", {"width_pt": 100, "height_pt": 100}, output_path,
        policy={"require_page_size_pt": (612, 792), "page_size_tolerance_pt": 1.0},
        evidence_dir=evidence_dir, dry_run=False, max_iterations=3,
    )

    assert result.receipt is not None
    assert result.receipt.iterations == 2
    assert result.receipt.status == CheckStatus.PASS
    assert result.receipt.operations[0]["args"] == {"width_pt": 100, "height_pt": 100}
    assert result.receipt.operations[0]["succeeded"] is True
    assert result.receipt.operations[1]["args"] == {"width_pt": 612, "height_pt": 792}
    assert not any("no automatic fixer" in lim for lim in result.receipt.limitations)


def test_fix_loop_gives_up_honestly_when_max_iterations_too_low(good_pdf, tmp_path):
    """With only 1 iteration allowed, the loop must not attempt a fix at
    all — it reports the failure as-is rather than silently succeeding."""
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        good_pdf, "fit_page_size", {"width_pt": 100, "height_pt": 100}, output_path,
        policy={"require_page_size_pt": (612, 792), "page_size_tolerance_pt": 1.0},
        evidence_dir=evidence_dir, dry_run=False, max_iterations=1,
    )

    assert result.receipt.iterations == 1
    assert result.receipt.status == CheckStatus.FAIL


def test_build_plan_rejects_args_violating_the_operation_schema(good_pdf, tmp_path):
    """OperationSpec.args_schema is now an enforced contract (Issue: SPEC
    alignment), not just descriptive metadata a caller could ignore —
    build_plan() validates args before ever calling adapter.plan()."""
    output_path = tmp_path / "out.pdf"
    with pytest.raises(ArtifactInputError) as exc_info:
        build_plan(good_pdf, "fit_page_size", {"width_pt": "not a number", "height_pt": 100}, output_path)
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_build_plan_rejects_missing_required_arg(good_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    with pytest.raises(ArtifactInputError) as exc_info:
        build_plan(good_pdf, "fit_page_size", {"width_pt": 100}, output_path)  # missing height_pt
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_build_plan_allows_valid_args_through(good_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    _ref, _adapter, plan = build_plan(good_pdf, "fit_page_size", {"width_pt": 100, "height_pt": 100}, output_path)
    assert plan.operation == "pdf.fit_page_size"


def test_render_result_reports_the_real_backend_not_a_hardcoded_one(good_png, tmp_path):
    """Regression guard: run_lifecycle() used to reconstruct LifecycleResult.render
    with a hardcoded backend="pypdfium2" regardless of which adapter actually
    rendered the evidence — every non-PDF-family adapter's receipt.render
    reported the wrong backend. Image's render() reports "Pillow"; this proves
    that real value now survives into the LifecycleResult unchanged."""
    output_path = tmp_path / "out.png"
    evidence_dir = tmp_path / "reports"

    result = run_lifecycle(
        good_png, "resize", {"width": 10, "height": 10}, output_path,
        evidence_dir=evidence_dir, dry_run=False,
    )

    assert result.render is not None
    assert result.render.backend == "Pillow"


def test_run_lifecycle_rejects_invalid_args_before_touching_disk(good_pdf, tmp_path):
    output_path = tmp_path / "out.pdf"
    evidence_dir = tmp_path / "reports"
    with pytest.raises(ArtifactInputError) as exc_info:
        run_lifecycle(
            good_pdf, "fit_page_size", {"width_pt": -5, "height_pt": 100}, output_path,
            evidence_dir=evidence_dir, dry_run=False,
        )
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"
    assert not output_path.exists()
    assert not evidence_dir.exists()
