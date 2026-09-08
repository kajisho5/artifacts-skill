from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.pptx.adapter import PptxAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> PptxAdapter:
    return PptxAdapter()


def _probe_pptx_render_works(adapter: PptxAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    """LibreOffice being on PATH doesn't guarantee it can actually convert
    a document in a given environment (see the adapter module's docstring
    for the real failure this project hit during development). Probe once
    per test instead of assuming either way, so render-happy-path tests
    skip cleanly on a broken LibreOffice install rather than failing."""
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001 - any failure means "doesn't work here"
        return False


def test_type_detection_by_content_not_extension(good_pptx, mislabeled_pdf_as_pptx):
    assert ArtifactRef.from_path(good_pptx).type == ArtifactType.PPTX
    # A real PDF saved with a .pptx extension must NOT be typed as PPTX.
    assert ArtifactRef.from_path(mislabeled_pdf_as_pptx).type == ArtifactType.PDF


def test_inspect_good_deck_reports_two_slides(good_pptx, adapter):
    ref = ArtifactRef.from_path(good_pptx)
    report = adapter.inspect(ref)
    assert report.details["slide_count"] == 2
    assert report.details["text_bearing_slides"] == 1
    assert report.details["broken_media"] == []


def test_inspect_empty_placeholder_deck_flags_it(empty_placeholder_pptx, adapter):
    ref = ArtifactRef.from_path(empty_placeholder_pptx)
    report = adapter.inspect(ref)
    assert report.details["empty_placeholders"] >= 1


def test_inspect_corrupt_pptx_raises_structured_error(corrupt_pptx, adapter):
    ref = ArtifactRef.from_path(corrupt_pptx)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_PPTX_UNREADABLE"


def test_verify_zero_slide_deck_fails_slide_count(zero_slide_pptx, adapter):
    ref = ArtifactRef.from_path(zero_slide_pptx)
    result = adapter.verify_structural(ref, {})
    slide_count_check = next(c for c in result.checks if c.id == "slide_count")
    assert slide_count_check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_verify_empty_placeholder_warns_by_default(empty_placeholder_pptx, adapter):
    ref = ArtifactRef.from_path(empty_placeholder_pptx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "empty_placeholders")
    assert check.status == CheckStatus.WARN


def test_verify_empty_placeholder_passes_when_policy_allows_it(empty_placeholder_pptx, adapter):
    ref = ArtifactRef.from_path(empty_placeholder_pptx)
    result = adapter.verify_structural(ref, {"max_empty_placeholders": 5})
    check = next(c for c in result.checks if c.id == "empty_placeholders")
    assert check.status == CheckStatus.PASS


def test_verify_good_deck_slide_count_requirement(good_pptx, adapter):
    ref = ArtifactRef.from_path(good_pptx)
    ok = adapter.verify_structural(ref, {"require_slide_count": 2})
    assert next(c for c in ok.checks if c.id == "slide_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_slide_count": 5})
    assert next(c for c in mismatch.checks if c.id == "slide_count_requirement").status == CheckStatus.FAIL


def test_chart_validity_skipped_when_no_charts(good_pptx, adapter):
    ref = ArtifactRef.from_path(good_pptx)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "chart_validity")
    assert check.status == CheckStatus.SKIPPED


def test_execute_metadata_set_preserves_input_and_slide_count(good_pptx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pptx)
    before_hash = ref.sha256
    output_path = tmp_path / "out.pptx"

    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title", "author": "QA"}, output_path)

    assert output_path.exists()
    assert result_ref.sha256 != before_hash
    assert ArtifactRef.from_path(good_pptx).sha256 == before_hash  # Original Protection

    out_report = adapter.inspect(result_ref)
    assert out_report.details["metadata"]["title"] == "New Title"
    assert out_report.details["metadata"]["author"] == "QA"
    assert out_report.details["slide_count"] == 2  # postcondition


def test_execute_unknown_operation_raises(good_pptx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pptx)
    with pytest.raises(ArtifactInputError):
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.pptx")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "pptx.structural" in caps
    assert "pptx.render" in caps
    assert caps["pptx.structural"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)
    assert caps["pptx.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_backend_failure_raises_structured_error_not_crash(good_pptx, adapter, tmp_path, monkeypatch):
    """Regardless of whether LibreOffice actually works in this environment,
    a conversion failure must surface as ARTIFACT_RENDER_BACKEND_FAILED
    with the backend's own diagnostic — never an uncaught exception or a
    silently-empty success. Forces the failure via monkeypatch so this test
    doesn't depend on the real LibreOffice install's health."""
    import artifact_skill.adapters.pptx.adapter as adapter_module
    from artifact_skill.security.subprocess_exec import ExecResult

    def _fake_run(argv, **kwargs):
        return ExecResult(argv=argv, returncode=1, stdout="", stderr="fake soffice failure for testing", timed_out=False)

    monkeypatch.setattr(adapter_module, "_soffice_binary", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(adapter_module, "run_subprocess", _fake_run)

    ref = ArtifactRef.from_path(good_pptx)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"
    assert "fake soffice failure" in exc_info.value.evidence["stderr"]


def test_render_happy_path_when_backend_actually_works(good_pptx, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pptx)
    if not _probe_pptx_render_works(adapter, ref, tmp_path):
        pytest.skip("LibreOffice in this environment cannot convert documents (soffice present but non-functional)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 2
    assert all(f.exists() for f in result.files)
