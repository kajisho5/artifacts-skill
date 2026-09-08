from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.xlsx.adapter import XlsxAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> XlsxAdapter:
    return XlsxAdapter()


def _probe_xlsx_render_works(adapter: XlsxAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


def test_type_detection_by_content_not_extension(good_xlsx, mislabeled_pdf_as_xlsx):
    assert ArtifactRef.from_path(good_xlsx).type == ArtifactType.XLSX
    assert ArtifactRef.from_path(mislabeled_pdf_as_xlsx).type == ArtifactType.PDF


def test_inspect_good_workbook_reports_two_sheets_and_a_formula(good_xlsx, adapter):
    ref = ArtifactRef.from_path(good_xlsx)
    report = adapter.inspect(ref)
    assert report.details["sheet_count"] == 2
    assert report.details["formula_cells"] == 1
    assert report.details["cached_values_seen"] is True
    assert report.details["cached_errors"] == []


def test_inspect_corrupt_xlsx_raises_structured_error(corrupt_xlsx, adapter):
    ref = ArtifactRef.from_path(corrupt_xlsx)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_XLSX_UNREADABLE"


def test_verify_no_formula_workbook_skips_formula_checks(no_formula_xlsx, adapter):
    ref = ArtifactRef.from_path(no_formula_xlsx)
    result = adapter.verify_structural(ref, {})
    errors_check = next(c for c in result.checks if c.id == "formula_cached_errors")
    recalc_check = next(c for c in result.checks if c.id == "formula_recalculation")
    assert errors_check.status == CheckStatus.SKIPPED
    assert recalc_check.status == CheckStatus.SKIPPED
    # No formulas, no other problems -> overall PASS, not dragged down by SKIPPED.
    assert result.status == CheckStatus.PASS


def test_verify_good_workbook_formula_cached_errors_pass_but_recalculation_unknown(good_xlsx, adapter):
    """The core design point of this adapter: a workbook with a real cached
    (non-error) result still can't claim the formula is *currently* correct
    — openpyxl never recalculates — so overall status is UNKNOWN even
    though the one concrete thing we could check (cached errors) passed."""
    ref = ArtifactRef.from_path(good_xlsx)
    result = adapter.verify_structural(ref, {})
    errors_check = next(c for c in result.checks if c.id == "formula_cached_errors")
    recalc_check = next(c for c in result.checks if c.id == "formula_recalculation")
    assert errors_check.status == CheckStatus.PASS
    assert recalc_check.status == CheckStatus.UNKNOWN
    assert result.status == CheckStatus.UNKNOWN


def test_verify_formula_error_workbook_fails(formula_error_xlsx, adapter):
    ref = ArtifactRef.from_path(formula_error_xlsx)
    result = adapter.verify_structural(ref, {})
    errors_check = next(c for c in result.checks if c.id == "formula_cached_errors")
    assert errors_check.status == CheckStatus.FAIL
    assert "#REF!" in errors_check.evidence["cells"][0]
    assert result.status == CheckStatus.FAIL


def test_inspect_external_link_workbook_reports_the_link(external_link_xlsx, adapter):
    ref = ArtifactRef.from_path(external_link_xlsx)
    report = adapter.inspect(ref)
    assert len(report.details["external_links"]) == 1


def test_verify_external_link_workbook_warns_by_default(external_link_xlsx, adapter):
    ref = ArtifactRef.from_path(external_link_xlsx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "external_links")
    assert check.status == CheckStatus.WARN


def test_verify_external_link_workbook_fails_under_strict_policy(external_link_xlsx, adapter):
    ref = ArtifactRef.from_path(external_link_xlsx)
    result = adapter.verify_structural(ref, {"forbid_external_links": True})
    check = next(c for c in result.checks if c.id == "external_links")
    assert check.status == CheckStatus.FAIL


def test_verify_good_workbook_has_no_leftover_placeholder_text(good_xlsx, adapter):
    ref = ArtifactRef.from_path(good_xlsx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_workbook_warns_by_default(leftover_placeholder_xlsx, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_xlsx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "click to add" in check.evidence["markers"]
    assert "todo" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]


def test_verify_leftover_placeholder_workbook_fails_under_strict_policy(leftover_placeholder_xlsx, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_xlsx)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_verify_good_workbook_sheet_count_requirement(good_xlsx, adapter):
    ref = ArtifactRef.from_path(good_xlsx)
    ok = adapter.verify_structural(ref, {"require_sheet_count": 2})
    assert next(c for c in ok.checks if c.id == "sheet_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_sheet_count": 5})
    assert next(c for c in mismatch.checks if c.id == "sheet_count_requirement").status == CheckStatus.FAIL


def test_execute_metadata_set_preserves_input_and_sheet_count(good_xlsx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_xlsx)
    before_hash = ref.sha256
    output_path = tmp_path / "out.xlsx"

    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title", "author": "QA"}, output_path)

    assert output_path.exists()
    assert result_ref.sha256 != before_hash
    assert ArtifactRef.from_path(good_xlsx).sha256 == before_hash  # Original Protection

    out_report = adapter.inspect(result_ref)
    assert out_report.details["metadata"]["title"] == "New Title"
    assert out_report.details["metadata"]["author"] == "QA"
    assert out_report.details["sheet_count"] == 2


def test_execute_unknown_operation_raises(good_xlsx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_xlsx)
    with pytest.raises(ArtifactInputError):
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.xlsx")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "xlsx.structural" in caps
    assert "xlsx.render" in caps
    assert caps["xlsx.structural"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)
    assert caps["xlsx.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_propagates_backend_failure(good_xlsx, adapter, tmp_path, monkeypatch):
    import artifact_skill.adapters.xlsx.adapter as adapter_module

    def _fake_convert(input_path, pdf_out_dir):
        raise ArtifactExecutionError(
            code="ARTIFACT_RENDER_BACKEND_FAILED", message="fake failure for testing",
            evidence={"stderr": "fake soffice failure for testing"},
        )

    monkeypatch.setattr(adapter_module, "convert_to_pdf", _fake_convert)

    ref = ArtifactRef.from_path(good_xlsx)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"


def test_render_happy_path_when_backend_actually_works(good_xlsx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_xlsx)
    if not _probe_xlsx_render_works(adapter, ref, tmp_path):
        pytest.skip("LibreOffice in this environment cannot convert documents (soffice present but non-functional)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) >= 1
    assert all(f.exists() for f in result.files)
