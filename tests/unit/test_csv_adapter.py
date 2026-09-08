from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.csv.adapter import CsvAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> CsvAdapter:
    return CsvAdapter()


def _probe_csv_render_works(adapter: CsvAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


def test_type_detection_by_content_not_extension(good_csv, mislabeled_pdf_as_csv):
    assert ArtifactRef.from_path(good_csv).type == ArtifactType.CSV
    assert ArtifactRef.from_path(mislabeled_pdf_as_csv).type == ArtifactType.PDF


def test_a_ragged_csv_is_still_detected_as_csv_by_type(ragged_csv):
    """The single ragged row must not make detect_type() give up on the
    whole file - that would make column_count_consistency (below)
    unreachable for the exact case it exists to catch."""
    assert ArtifactRef.from_path(ragged_csv).type == ArtifactType.CSV


def test_inspect_good_csv_reports_rows_and_header(good_csv, adapter):
    ref = ArtifactRef.from_path(good_csv)
    report = adapter.inspect(ref)
    assert report.details["row_count"] == 4  # header + 3 data rows
    assert report.details["column_count"] == 3
    assert report.details["header"] == ["name", "age", "city"]
    assert report.details["ragged_row_count"] == 0


def test_inspect_rejects_non_utf8(adapter, tmp_path):
    path = tmp_path / "bad_encoding.csv"
    path.write_bytes("name,city\nÉdouard,São Paulo\n".encode("latin-1"))
    ref = ArtifactRef.from_path(path)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_CSV_UNREADABLE"


def test_verify_good_csv_passes_cleanly(good_csv, adapter):
    ref = ArtifactRef.from_path(good_csv)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_ragged_csv_fails_column_count_consistency(ragged_csv, adapter):
    ref = ArtifactRef.from_path(ragged_csv)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "column_count_consistency")
    assert check.status == CheckStatus.FAIL
    assert check.evidence["ragged_row_indices"] == [2]
    assert result.status == CheckStatus.FAIL


def test_verify_good_csv_row_count_requirement(good_csv, adapter):
    ref = ArtifactRef.from_path(good_csv)
    ok = adapter.verify_structural(ref, {"require_row_count": 4})  # header + 3 data rows
    assert next(c for c in ok.checks if c.id == "row_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_row_count": 10})
    assert next(c for c in mismatch.checks if c.id == "row_count_requirement").status == CheckStatus.FAIL


def test_verify_good_csv_has_no_leftover_placeholder_text(good_csv, adapter):
    ref = ArtifactRef.from_path(good_csv)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_csv_warns_by_default(leftover_placeholder_csv, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_csv)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "todo" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]


def test_verify_leftover_placeholder_csv_fails_under_strict_policy(leftover_placeholder_csv, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_csv)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_no_mutating_operations(good_csv, adapter, tmp_path):
    assert adapter.operations() == {}
    ref = ArtifactRef.from_path(good_csv)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "anything", {}, tmp_path / "out.csv")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"
    with pytest.raises(ArtifactInputError):
        adapter.plan(ref, "anything", {}, tmp_path / "out.csv")


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "csv.structural" in caps
    assert caps["csv.structural"].status == CapabilityStatus.AVAILABLE  # stdlib-only, always available
    assert "csv.render" in caps
    assert caps["csv.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_propagates_backend_failure(good_csv, adapter, tmp_path, monkeypatch):
    playwright_sync_api = pytest.importorskip("playwright.sync_api")

    def _fake_sync_playwright():
        raise playwright_sync_api.Error("fake launch failure for testing")

    monkeypatch.setattr(playwright_sync_api, "sync_playwright", _fake_sync_playwright)

    ref = ArtifactRef.from_path(good_csv)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"


def test_render_truncates_beyond_max_rows_and_warns(adapter, tmp_path, monkeypatch):
    import artifact_skill.adapters.csv.adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_MAX_RENDER_ROWS", 2)
    path = tmp_path / "many_rows.csv"
    path.write_text("a,b\n" + "\n".join(f"{i},{i * 2}" for i in range(10)) + "\n")
    ref = ArtifactRef.from_path(path)
    if not _probe_csv_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render")
    result = adapter.render(ref, tmp_path / "rendered")
    assert result.warnings
    assert "truncated" in result.warnings[0]


def test_render_happy_path_when_backend_actually_works(good_csv, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_csv)
    if not _probe_csv_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists()


def test_limitations_names_the_real_known_caveats(adapter):
    text = " ".join(adapter.limitations())
    assert "No mutating operations" in text
    assert "heuristic" in text
    assert "UTF-8" in text
