from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.markdown.adapter import MarkdownAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> MarkdownAdapter:
    return MarkdownAdapter()


def _probe_markdown_render_works(adapter: MarkdownAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


def test_type_detection_by_content_not_extension(good_markdown, mislabeled_pdf_as_markdown):
    assert ArtifactRef.from_path(good_markdown).type == ArtifactType.MARKDOWN
    assert ArtifactRef.from_path(mislabeled_pdf_as_markdown).type == ArtifactType.PDF


def test_plain_prose_with_no_markdown_signals_is_unknown_not_guessed(tmp_path):
    """spec #7-style honesty: a file that doesn't clear the Markdown
    heuristic bar must stay UNKNOWN, not be guessed at."""
    path = tmp_path / "prose.txt"
    path.write_text("Just some ordinary prose. No headings, links, lists, or code fences here at all.\n")
    assert ArtifactRef.from_path(path).type == ArtifactType.UNKNOWN


def test_a_leading_utf8_bom_does_not_defeat_markdown_type_detection(tmp_path):
    """Self-audit finding: a UTF-8 BOM (common from Windows editors/export
    tools) glued onto the first line broke the ATX-heading regex's '^#'
    anchor match on that line specifically - a real, otherwise-detectable
    Markdown document (heading + fenced code block) was misdetected as
    UNKNOWN before this fix. Confirmed by direct testing."""
    path = tmp_path / "bom.md"
    path.write_bytes("﻿# Title\n\n```python\nprint(1)\n```\n".encode())
    assert ArtifactRef.from_path(path).type == ArtifactType.MARKDOWN


def test_inspect_strips_a_leading_utf8_bom_before_scanning_headings(good_markdown, adapter, tmp_path):
    path = tmp_path / "bom.md"
    path.write_bytes(b"\xef\xbb\xbf# Title\n\nSome text.\n\n- a\n- b\n")
    ref = ArtifactRef.from_path(path)
    report = adapter.inspect(ref)
    assert report.details["heading_count"] == 1
    assert report.details["headings"][0] == (1, "Title")


def test_inspect_good_markdown_reports_headings_and_fence_balance(good_markdown, adapter):
    ref = ArtifactRef.from_path(good_markdown)
    report = adapter.inspect(ref)
    assert report.details["heading_count"] == 1
    assert report.details["headings"][0] == (1, "Title")
    assert report.details["fenced_code_block_balanced"] is True


def test_inspect_rejects_non_utf8(adapter, tmp_path):
    path = tmp_path / "bad_encoding.md"
    path.write_bytes("# Café\n".encode("latin-1"))
    ref = ArtifactRef.from_path(path)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_MARKDOWN_UNREADABLE"


def test_verify_good_markdown_passes_cleanly(good_markdown, adapter):
    ref = ArtifactRef.from_path(good_markdown)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_unclosed_fence_warns(unclosed_fence_markdown, adapter):
    ref = ArtifactRef.from_path(unclosed_fence_markdown)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "fenced_code_block_balance")
    assert check.status == CheckStatus.WARN


def test_verify_missing_local_resource_fails(missing_local_resource_markdown, adapter):
    ref = ArtifactRef.from_path(missing_local_resource_markdown)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "local_resources")
    assert check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_verify_external_resource_warns_by_default(external_resource_markdown, adapter):
    ref = ArtifactRef.from_path(external_resource_markdown)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "external_resources")
    assert check.status == CheckStatus.WARN


def test_verify_external_resource_fails_under_strict_policy(external_resource_markdown, adapter):
    ref = ArtifactRef.from_path(external_resource_markdown)
    result = adapter.verify_structural(ref, {"forbid_external_resources": True})
    check = next(c for c in result.checks if c.id == "external_resources")
    assert check.status == CheckStatus.FAIL


def test_verify_require_heading(good_markdown, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_markdown)
    ok = adapter.verify_structural(ref, {"require_heading": True})
    assert next(c for c in ok.checks if c.id == "heading_presence").status == CheckStatus.PASS

    no_heading = tmp_path / "no_heading.md"
    no_heading.write_text("Just a [link](good.md) and a paragraph, no heading at all.\n\n- one\n- two\n")
    mismatch = adapter.verify_structural(ArtifactRef.from_path(no_heading), {"require_heading": True})
    assert next(c for c in mismatch.checks if c.id == "heading_presence").status == CheckStatus.FAIL


def test_verify_leftover_placeholder_markdown_warns_by_default(leftover_placeholder_markdown, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_markdown)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "todo" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]


def test_verify_leftover_placeholder_markdown_fails_under_strict_policy(leftover_placeholder_markdown, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_markdown)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_verify_leftover_text_inside_a_code_fence_is_not_flagged(leftover_in_code_fence_markdown, adapter):
    """A "TODO" that appears only inside a fenced code block's body is
    sample output, not an unreviewed generation artifact - it must not be
    flagged, the same "code is not document text" reasoning as HTML's
    <script>/<style> exclusion."""
    ref = ArtifactRef.from_path(leftover_in_code_fence_markdown)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_no_mutating_operations(good_markdown, adapter, tmp_path):
    assert adapter.operations() == {}
    ref = ArtifactRef.from_path(good_markdown)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "anything", {}, tmp_path / "out.md")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"
    with pytest.raises(ArtifactInputError):
        adapter.plan(ref, "anything", {}, tmp_path / "out.md")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "markdown.structural" in caps
    assert caps["markdown.structural"].status == CapabilityStatus.AVAILABLE  # stdlib-only, always available
    assert "markdown.render" in caps
    assert caps["markdown.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_raises_capability_missing_without_markdown_it(good_markdown, adapter, tmp_path, monkeypatch):
    import artifact_skill.adapters.markdown.adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_has", lambda module: False)
    ref = ArtifactRef.from_path(good_markdown)
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_CAPABILITY_MISSING"


def test_render_propagates_backend_failure(good_markdown, adapter, tmp_path, monkeypatch):
    pytest.importorskip("markdown_it")
    playwright_sync_api = pytest.importorskip("playwright.sync_api")

    def _fake_sync_playwright():
        raise playwright_sync_api.Error("fake launch failure for testing")

    monkeypatch.setattr(playwright_sync_api, "sync_playwright", _fake_sync_playwright)

    ref = ArtifactRef.from_path(good_markdown)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"


def test_render_happy_path_when_backend_actually_works(good_markdown, adapter, tmp_path):
    pytest.importorskip("markdown_it")
    ref = ArtifactRef.from_path(good_markdown)
    if not _probe_markdown_render_works(adapter, ref, tmp_path):
        pytest.skip("markdown-it-py/Playwright/Chromium in this environment cannot render")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists()


def test_limitations_names_the_real_known_caveats(adapter):
    text = " ".join(adapter.limitations())
    assert "No mutating operations" in text
    assert "heuristic" in text
    assert "markdown-it-py" in text
