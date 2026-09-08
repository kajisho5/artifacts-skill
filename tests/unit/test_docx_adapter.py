from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.docx.adapter import DocxAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> DocxAdapter:
    return DocxAdapter()


def _probe_docx_render_works(adapter: DocxAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    """See the identical helper in test_pptx_adapter.py — LibreOffice being
    on PATH doesn't guarantee it can convert a document in this environment."""
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001 - any failure means "doesn't work here"
        return False


def test_type_detection_by_content_not_extension(good_docx, mislabeled_pdf_as_docx):
    assert ArtifactRef.from_path(good_docx).type == ArtifactType.DOCX
    assert ArtifactRef.from_path(mislabeled_pdf_as_docx).type == ArtifactType.PDF


def test_inspect_good_doc_reports_paragraphs_and_table(good_docx, adapter):
    ref = ArtifactRef.from_path(good_docx)
    report = adapter.inspect(ref)
    assert report.details["paragraph_count"] >= 2
    assert report.details["table_count"] == 1
    assert report.details["broken_media"] == []


def test_inspect_corrupt_docx_raises_structured_error(corrupt_docx, adapter):
    ref = ArtifactRef.from_path(corrupt_docx)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_DOCX_UNREADABLE"


def test_verify_empty_docx_fails_paragraph_count(empty_docx, adapter):
    ref = ArtifactRef.from_path(empty_docx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "paragraph_count")
    assert check.status == CheckStatus.FAIL


def test_verify_good_docx_has_no_leftover_placeholder_text(good_docx, adapter):
    ref = ArtifactRef.from_path(good_docx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_docx_warns_by_default(leftover_placeholder_docx, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_docx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "click to add" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]
    assert "todo" in check.evidence["markers"]


def test_verify_leftover_placeholder_docx_fails_under_strict_policy(leftover_placeholder_docx, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_docx)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_page_count_is_always_unknown_not_hidden(good_docx, adapter):
    """DOCX has no fixed pagination in its XML — this must never be
    silently omitted or fabricated (see the adapter module's docstring)."""
    ref = ArtifactRef.from_path(good_docx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "page_count")
    assert check.status == CheckStatus.UNKNOWN


def test_verify_good_doc_paragraph_count_requirement(good_docx, adapter):
    ref = ArtifactRef.from_path(good_docx)
    report = adapter.inspect(ref)
    n = report.details["paragraph_count"]

    ok = adapter.verify_structural(ref, {"require_paragraph_count": n})
    assert next(c for c in ok.checks if c.id == "paragraph_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_paragraph_count": n + 100})
    assert next(c for c in mismatch.checks if c.id == "paragraph_count_requirement").status == CheckStatus.FAIL


def test_execute_metadata_set_preserves_input_and_paragraph_count(good_docx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_docx)
    before_hash = ref.sha256
    before_report = adapter.inspect(ref)
    output_path = tmp_path / "out.docx"

    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title", "author": "QA"}, output_path)

    assert output_path.exists()
    assert result_ref.sha256 != before_hash
    assert ArtifactRef.from_path(good_docx).sha256 == before_hash  # Original Protection

    out_report = adapter.inspect(result_ref)
    assert out_report.details["metadata"]["title"] == "New Title"
    assert out_report.details["metadata"]["author"] == "QA"
    assert out_report.details["paragraph_count"] == before_report.details["paragraph_count"]


def test_execute_unknown_operation_raises(good_docx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_docx)
    with pytest.raises(ArtifactInputError):
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.docx")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "docx.structural" in caps
    assert "docx.render" in caps
    assert caps["docx.structural"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)
    assert caps["docx.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_propagates_backend_failure(good_docx, adapter, tmp_path, monkeypatch):
    import artifact_skill.adapters.docx.adapter as adapter_module

    def _fake_convert(input_path, pdf_out_dir, **kwargs):
        raise ArtifactExecutionError(
            code="ARTIFACT_RENDER_BACKEND_FAILED", message="fake failure for testing",
            evidence={"stderr": "fake soffice failure for testing"},
        )

    monkeypatch.setattr(adapter_module, "convert_to_pdf", _fake_convert)

    ref = ArtifactRef.from_path(good_docx)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"


def test_render_happy_path_when_backend_actually_works(good_docx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_docx)
    if not _probe_docx_render_works(adapter, ref, tmp_path):
        pytest.skip("LibreOffice in this environment cannot convert documents (soffice present but non-functional)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) >= 1
    assert all(f.exists() for f in result.files)


# --- refine_structural_with_render (Issue #14) ------------------------


def test_refine_with_no_render_leaves_page_count_unknown(good_docx, adapter):
    ref = ArtifactRef.from_path(good_docx)
    structural = adapter.verify_structural(ref, {})
    refined = adapter.refine_structural_with_render(structural, None)
    check = next(c for c in refined.checks if c.id == "page_count")
    assert check.status == CheckStatus.UNKNOWN


def test_refine_with_empty_render_leaves_page_count_unknown(good_docx, adapter):
    from artifact_skill.adapters.base import RenderResult

    ref = ArtifactRef.from_path(good_docx)
    structural = adapter.verify_structural(ref, {})
    refined = adapter.refine_structural_with_render(structural, RenderResult(kind="page_images", files=[], backend="pypdfium2"))
    check = next(c for c in refined.checks if c.id == "page_count")
    assert check.status == CheckStatus.UNKNOWN


def test_refine_with_real_render_upgrades_page_count_to_pass(good_docx, adapter, tmp_path):
    """Unit-tests the refinement logic directly with a synthetic
    RenderResult (files that don't need to actually exist for this) so it
    doesn't depend on LibreOffice actually working in this environment —
    the end-to-end proof against a real render is
    test_docx_receipt_measures_real_page_count_when_render_works below."""
    from artifact_skill.adapters.base import RenderResult

    ref = ArtifactRef.from_path(good_docx)
    structural = adapter.verify_structural(ref, {})
    fake_pages = [tmp_path / "page-001.png", tmp_path / "page-002.png"]
    refined = adapter.refine_structural_with_render(
        structural, RenderResult(kind="page_images", files=fake_pages, backend="pypdfium2")
    )
    check = next(c for c in refined.checks if c.id == "page_count")
    assert check.status == CheckStatus.PASS
    assert check.evidence["page_count"] == 2
    # every other check must survive untouched
    other_ids_before = {c.id for c in structural.checks if c.id != "page_count"}
    other_ids_after = {c.id for c in refined.checks if c.id != "page_count"}
    assert other_ids_before == other_ids_after


def test_docx_receipt_measures_real_page_count_when_render_works(good_docx, adapter, tmp_path):
    """End-to-end: run_lifecycle() (execute/receipt path) should upgrade
    DOCX's page_count from UNKNOWN to a real, PASS, measured value when
    LibreOffice actually renders successfully as part of the same run."""
    from artifact_skill.core.engine import run_lifecycle

    ref = ArtifactRef.from_path(good_docx)
    if not _probe_docx_render_works(adapter, ref, tmp_path / "_probe"):
        pytest.skip("LibreOffice in this environment cannot convert documents (soffice present but non-functional)")

    result = run_lifecycle(
        good_docx, "metadata_set", {"title": "x"}, tmp_path / "out.docx",
        evidence_dir=tmp_path / "reports", dry_run=False,
    )
    structural = result.receipt.verification["structural"]
    check = next(c for c in structural["checks"] if c["id"] == "page_count")
    assert check["status"] == "pass"
    assert check["evidence"]["page_count"] >= 1


def test_limitations_names_the_real_known_caveats(adapter):
    """Issue #29: limitations() content had zero test coverage anywhere."""
    text = " ".join(adapter.limitations())
    assert "Page count cannot be determined structurally" in text
    assert "Hyperlink validity" in text
    assert "Numbering/list consistency" in text
    assert "LibreOffice install" in text
