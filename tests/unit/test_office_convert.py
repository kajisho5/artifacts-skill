"""Unit tests for the shared LibreOffice-conversion helper
(rendering/office_convert.py), used by every Office-family adapter that
renders via `format -> PDF -> pypdfium2 page images` (PPTX, DOCX, and
XLSX once it lands). Adapter-level render() tests only need to check that
they call this correctly and propagate its errors — the conversion logic
itself is fully covered here, once, instead of per-adapter.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError
from artifact_skill.rendering import office_convert
from artifact_skill.security.limits import Limits
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
    assert "no PDF output at all" in exc_info.value.message


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


def test_convert_to_pdf_ignores_a_stray_unrelated_pdf_already_in_the_out_dir(tmp_path, monkeypatch):
    """Self-audit finding (FIX_PROMPT P3-4): glob("*.pdf")[0]'s match
    order is arbitrary - a stray PDF already sitting in pdf_out_dir
    (e.g. left over from a prior failed run sharing the directory) could
    be silently returned as if it were this call's real output. Confirmed
    the fix by simulating soffice failing to produce ANY real output
    while a decoy with an unrelated name is already present."""
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "some_other_document.pdf").write_bytes(b"%PDF-1.4 decoy, not this call's output")

    def _fake_run(argv, **kwargs):
        return ExecResult(argv=argv, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    with pytest.raises(ArtifactExecutionError) as exc_info:
        office_convert.convert_to_pdf(tmp_path / "input.pptx", out_dir)
    assert exc_info.value.code == "ARTIFACT_RENDER_BACKEND_FAILED"
    assert "input.pdf" in exc_info.value.message
    assert "some_other_document.pdf" in exc_info.value.message


def test_convert_to_pdf_forwards_a_custom_limits_to_the_subprocess_call(tmp_path, monkeypatch):
    """Issue #28: a caller-supplied Limits override must actually reach
    run_subprocess() (which reads limits.subprocess_timeout_seconds) -
    previously convert_to_pdf() didn't accept a limits parameter at all,
    so run_subprocess() always fell back to its own DEFAULT_LIMITS
    regardless of what a caller further up the stack wanted."""
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")
    out_dir = tmp_path / "out"
    seen_kwargs = {}

    def _fake_run(argv, **kwargs):
        seen_kwargs.update(kwargs)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "input.pdf").write_bytes(b"%PDF-1.4 fake")
        return ExecResult(argv=argv, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    custom_limits = Limits(subprocess_timeout_seconds=7)
    office_convert.convert_to_pdf(tmp_path / "input.pptx", out_dir, limits=custom_limits)
    assert seen_kwargs["limits"] is custom_limits


# --- UserInstallation is a real file:// URI, not a naive concatenation ----
# (self-audit finding, FIX_PROMPT P1-3: f"file://{profile_dir}" is wrong on
# Windows - a Windows file:// URI needs "file:///C:/..." (an extra slash,
# backslash-to-slash conversion), which naive concatenation never produces
# - and doesn't percent-encode a POSIX path containing spaces/'#'/'?'
# either. Path.as_uri() handles both correctly.)


def test_user_installation_arg_is_a_real_file_uri(tmp_path, monkeypatch):
    monkeypatch.setattr(office_convert, "soffice_binary", lambda: "/usr/bin/soffice")
    out_dir = tmp_path / "out"
    seen_argv: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        seen_argv.append(argv)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "input.pdf").write_bytes(b"%PDF-1.4 fake")
        return ExecResult(argv=argv, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(office_convert, "run_subprocess", _fake_run)

    office_convert.convert_to_pdf(tmp_path / "input.pptx", out_dir)

    user_installation_arg = next(a for a in seen_argv[0] if a.startswith("-env:UserInstallation="))
    uri = user_installation_arg.removeprefix("-env:UserInstallation=")
    assert uri.startswith("file:///")
    assert " " not in uri
    # The profile dir is a plain mkdtemp() path (no special characters) in
    # this test, so round-tripping through Path(...).as_uri() must match
    # exactly - proving the real code path used .as_uri(), not a lookalike.
    profile_dir = uri.removeprefix("file://")
    from urllib.parse import unquote

    assert Path(unquote(profile_dir)).as_uri() == uri


def test_windows_style_profile_path_produces_a_correct_windows_file_uri():
    """Can't run convert_to_pdf() against a real WindowsPath from Linux CI
    (tempfile.TemporaryDirectory() always yields a native path), so this
    verifies the same stdlib primitive the fix relies on - PurePath.as_uri()
    - does the right thing for a Windows-shaped absolute path, the same
    "verify Windows-path logic explicitly rather than just trust it" pattern
    security/subprocess_exec.py already uses via ntpath for its own
    Windows-specific executable-name parsing."""
    uri = PureWindowsPath(r"C:\Users\me\AppData\Local\Temp\soffice profile #1").as_uri()
    assert uri == "file:///C:/Users/me/AppData/Local/Temp/soffice%20profile%20%231"
