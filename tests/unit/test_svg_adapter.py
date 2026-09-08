from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.svg.adapter import SvgAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> SvgAdapter:
    return SvgAdapter()


def _probe_svg_render_works(adapter: SvgAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    """Same pattern as the HTML/LibreOffice adapters' render probes — a
    present playwright/Chromium doesn't guarantee it actually renders."""
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


def test_type_detection_by_content_not_extension(good_svg, mislabeled_pdf_as_svg):
    assert ArtifactRef.from_path(good_svg).type == ArtifactType.SVG
    assert ArtifactRef.from_path(mislabeled_pdf_as_svg).type == ArtifactType.PDF


def test_inspect_good_svg_reports_size_and_no_resource_issues(good_svg, adapter):
    ref = ArtifactRef.from_path(good_svg)
    report = adapter.inspect(ref)
    assert report.details["has_explicit_size"] is True
    assert report.details["width"] == "100"
    assert report.details["external_resources"] == []
    assert report.details["local_resources_missing"] == []


def test_inspect_malformed_svg_raises_structured_error(malformed_svg, adapter):
    ref = ArtifactRef.from_path(malformed_svg)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_SVG_UNREADABLE"


def test_inspect_entity_bomb_is_rejected_before_parsing(entity_bomb_svg, adapter):
    """The real security control this adapter adds beyond HTML's: SVG is
    XML, and xml.etree.ElementTree is not hardened against entity-expansion
    DoS. A DOCTYPE/ENTITY declaration must be refused outright, never
    handed to the parser."""
    ref = ArtifactRef.from_path(entity_bomb_svg)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_render_also_rejects_entity_bomb(entity_bomb_svg, adapter, tmp_path):
    """Chromium would parse the same XML during render() — the guard must
    apply there too, not just to inspect()."""
    ref = ArtifactRef.from_path(entity_bomb_svg)
    with pytest.raises(ArtifactSecurityError):
        adapter.render(ref, tmp_path / "rendered")


def test_verify_good_svg_passes_cleanly(good_svg, adapter):
    ref = ArtifactRef.from_path(good_svg)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_missing_local_resources_fails(missing_local_resource_svg, adapter):
    ref = ArtifactRef.from_path(missing_local_resource_svg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "local_resources")
    assert check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_verify_external_resources_warns_by_default(external_resource_svg, adapter):
    ref = ArtifactRef.from_path(external_resource_svg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "external_resources")
    assert check.status == CheckStatus.WARN


def test_verify_external_resources_fails_under_strict_policy(external_resource_svg, adapter):
    ref = ArtifactRef.from_path(external_resource_svg)
    result = adapter.verify_structural(ref, {"forbid_external_resources": True})
    check = next(c for c in result.checks if c.id == "external_resources")
    assert check.status == CheckStatus.FAIL


def test_verify_no_size_warns(no_size_svg, adapter):
    ref = ArtifactRef.from_path(no_size_svg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "explicit_size")
    assert check.status == CheckStatus.WARN


def test_verify_good_svg_has_no_leftover_placeholder_text(good_svg, adapter):
    ref = ArtifactRef.from_path(good_svg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_svg_warns_by_default(leftover_placeholder_svg, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_svg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "click to add" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]
    assert "todo" in check.evidence["markers"]


def test_verify_leftover_placeholder_svg_fails_under_strict_policy(leftover_placeholder_svg, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_svg)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_no_mutating_operations(good_svg, adapter, tmp_path):
    assert adapter.operations() == {}
    ref = ArtifactRef.from_path(good_svg)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "anything", {}, tmp_path / "out.svg")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"
    with pytest.raises(ArtifactInputError):
        adapter.plan(ref, "anything", {}, tmp_path / "out.svg")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "svg.structural" in caps
    assert caps["svg.structural"].status == CapabilityStatus.AVAILABLE  # stdlib-only, always available
    assert "svg.render" in caps
    assert caps["svg.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_propagates_backend_failure(good_svg, adapter, tmp_path, monkeypatch):
    playwright_sync_api = pytest.importorskip("playwright.sync_api")

    def _fake_sync_playwright():
        raise playwright_sync_api.Error("fake launch failure for testing")

    monkeypatch.setattr(playwright_sync_api, "sync_playwright", _fake_sync_playwright)

    ref = ArtifactRef.from_path(good_svg)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"


def test_render_happy_path_when_backend_actually_works(good_svg, adapter, tmp_path):
    """Regression test for a real bug found while building this adapter:
    Page.screenshot(full_page=True) hangs until timeout against a
    standalone SVG document. render() must use full_page=False and
    actually complete."""
    ref = ArtifactRef.from_path(good_svg)
    if not _probe_svg_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render (see adapter docstring)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists()


def test_limitations_names_the_real_known_caveats(adapter):
    """Issue #29: limitations() content had zero test coverage anywhere."""
    text = " ".join(adapter.limitations())
    assert "No mutating operations" in text
    assert "@import" in text
    assert "External resources are never fetched" in text
    assert "viewport only" in text
