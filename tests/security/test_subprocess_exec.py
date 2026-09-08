from __future__ import annotations

import shutil

import pytest

from artifact_skill.core.errors import ArtifactExecutionError, ArtifactSecurityError
from artifact_skill.security.subprocess_exec import _executable_basename, run


def test_rejects_non_allowlisted_executable():
    with pytest.raises(ArtifactSecurityError) as exc_info:
        run(["rm", "-rf", "/"], allowlist={"echo"})
    assert exc_info.value.code == "ARTIFACT_SUBPROCESS_NOT_ALLOWLISTED"


def test_rejects_empty_argv():
    with pytest.raises(ArtifactSecurityError):
        run([], allowlist={"echo"})


def test_rejects_non_string_argv_entries():
    with pytest.raises(ArtifactSecurityError):
        run(["echo", 123], allowlist={"echo"})  # type: ignore[list-item]


def test_missing_executable_raises_execution_error():
    with pytest.raises(ArtifactExecutionError) as exc_info:
        run(["definitely-not-a-real-binary-xyz"], allowlist={"definitely-not-a-real-binary-xyz"})
    assert exc_info.value.code == "ARTIFACT_EXECUTABLE_NOT_FOUND"


def test_allowlisted_and_present_executable_runs():
    result = run(["echo", "hello"], allowlist={"echo"})
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_timeout_is_reported_not_raised():
    result = run(["sleep", "5"], allowlist={"sleep"}, timeout=1)
    assert result.timed_out is True


# --- cross-platform allowlist matching (Issue #10 audit) -----------------
#
# Adapters write allowlists using platform-neutral names (e.g. "soffice"),
# but an already-resolved path — what shutil.which() returns, and what
# rendering/office_convert.py passes as argv[0] — carries a Windows
# executable extension (soffice.exe) that a bare allowlist name never
# would. Without stripping it, a correct allowlist would reject every
# resolved executable on Windows. This project's CI only runs on
# ubuntu-latest, so a real Windows exercise isn't possible here — but
# _executable_basename() explicitly parses backslash paths with `ntpath`
# regardless of host OS (rather than relying on pathlib.Path, which only
# understands "\\" when Python itself runs on Windows), which is exactly
# what makes the Windows-shaped cases below testable, and passing, on
# this Linux CI.


@pytest.mark.parametrize(
    "argv0,expected",
    [
        ("soffice", "soffice"),
        ("/usr/bin/soffice", "soffice"),
        (r"C:\Program Files\LibreOffice\program\soffice.exe", "soffice"),
        (r"C:\Program Files\LibreOffice\program\soffice.EXE", "soffice"),
        ("soffice.exe", "soffice"),
        ("weird.name.exe", "weird.name"),  # only the final extension is stripped
    ],
)
def test_executable_basename_strips_windows_extensions(argv0, expected):
    assert _executable_basename(argv0) == expected


def test_windows_style_resolved_path_passes_a_bare_name_allowlist():
    """Regression guard for the bug this fix closes: before
    _executable_basename() existed, Path(argv0).name alone would compare
    "soffice.exe" against {"soffice"} and always fail on Windows."""
    windows_style_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
    assert _executable_basename(windows_style_path) in {"soffice"}
    # Sanity: the same helper is a no-op for the actual POSIX executable
    # this test can run for real in this (Linux) CI environment.
    real_echo = shutil.which("echo")
    assert real_echo is not None
    assert _executable_basename(real_echo) == "echo"
