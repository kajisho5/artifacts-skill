"""Unit tests for the shared LibreOffice-conversion helper
(rendering/office_convert.py), used by every Office-family adapter that
renders via `format -> PDF -> pypdfium2 page images` (PPTX, DOCX, and
XLSX once it lands). Adapter-level render() tests only need to check that
they call this correctly and propagate its errors — the conversion logic
itself is fully covered here, once, instead of per-adapter.
"""

from __future__ import annotations

import pytest

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError
from artifact_skill.rendering import office_convert
from artifact_skill.security.subprocess_exec import ExecResult


def test_convert_to_pdf_raises_capability_error_when_soffice_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: None)
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        office_convert.convert_to_pdf(tmp_path / "input.pptx", tmp_path / "out")
    assert exc_info.value.evidence["capability_id"] == "render"


def test_convert_to_pdf_raises_backend_failed_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")

    def _fake_run(argv, **kwargs):
        return ExecResult(argv=argv, returncode=1, stdout="", stderr="fake failure", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    with pytest.raises(ArtifactExecutionError) as exc_info:
        office_convert.convert_to_pdf(tmp_path / "input.pptx", tmp_path / "out")
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"
    assert exc_info.value.evidence["stderr"] == "fake failure"


def test_convert_to_pdf_raises_backend_failed_when_exit_zero_but_no_output(tmp_path, monkeypatch):
    """The real failure mode this project hit: soffice launches, exits 0,
    but produces no PDF (see docs/adapters.md's PPTX section)."""
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")

    def _fake_run(argv, **kwargs):
        return ExecResult(argv=argv, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    with pytest.raises(ArtifactExecutionError) as exc_info:
        office_convert.convert_to_pdf(tmp_path / "input.pptx", tmp_path / "out")
    assert "produced no PDF output" in exc_info.value.message


def test_convert_to_pdf_returns_produced_pdf_path(tmp_path, monkeypatch):
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")
    out_dir = tmp_path / "out"

    def _fake_run(argv, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "input.pdf").write_bytes(b"%PDF-1.4 fake")
        return ExecResult(argv=argv, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    result = office_convert.convert_to_pdf(tmp_path / "input.pptx", out_dir)
    assert result == out_dir / "input.pdf"
