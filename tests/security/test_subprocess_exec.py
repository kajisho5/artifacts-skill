from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from artifact_skill.core.errors import ArtifactExecutionError, ArtifactSecurityError
from artifact_skill.security.subprocess_exec import _executable_basename, run, treat_sigterm_as_interrupt


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


# --- interrupt safety: no orphaned children on SIGTERM (Issue #25) --------
#
# Verified empirically (not just by reading the code) against CPython's own
# subprocess.py: subprocess.run() already kills its child on any Python-
# level exception escaping communicate() - including KeyboardInterrupt,
# which is exactly what SIGINT raises by default - so a real Ctrl-C could
# never orphan a child even before this fix. SIGTERM is different: its
# default disposition terminates the process immediately with **no**
# Python exception at all, so nothing downstream (no try/finally, no
# except clause, anywhere) ever runs. These tests spawn a real child
# Python process (not the pytest process itself - sending SIGTERM to the
# runner would kill the test run) so a real SIGTERM can be sent to it
# without disturbing the test session.

_CHILD_SCRIPT = """
import sys
sys.path.insert(0, {src_path!r})
from artifact_skill.security.subprocess_exec import run
if {install_handler}:
    from artifact_skill.security.subprocess_exec import treat_sigterm_as_interrupt
    treat_sigterm_as_interrupt()
print("READY", flush=True)
run(["sleep", "30"], allowlist={{"sleep"}}, timeout=25)
"""


def _run_child_and_sigterm_it(install_handler: bool, tmp_path) -> bool:
    """Returns True iff the grandchild `sleep` process is still alive a
    moment after the direct child received SIGTERM."""
    src_path = str((__import__("artifact_skill").__file__).rsplit("/artifact_skill/", 1)[0])
    script = tmp_path / "child.py"
    script.write_text(_CHILD_SCRIPT.format(src_path=src_path, install_handler=install_handler))

    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        ready_line = proc.stdout.readline()
        assert ready_line.strip() == "READY", f"child didn't start cleanly: {ready_line!r}"
        time.sleep(0.3)  # let the grandchild `sleep` actually spawn
        before = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
        assert before, "grandchild `sleep` process never started"
        grandchild_pid = int(before[0])

        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        time.sleep(0.3)  # give the OS a moment to reap/report
        return os.path.exists(f"/proc/{grandchild_pid}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        # Best-effort: never leave a real orphan behind from this test itself.
        subprocess.run(["pkill", "-9", "-f", "sleep 30"], capture_output=True)


@pytest.mark.skipif(sys.platform == "win32", reason="signal.SIGTERM semantics tested here are POSIX-specific")
def test_sigterm_without_the_handler_orphans_the_child(tmp_path):
    """Establishes the bug is real before asserting the fix works below."""
    assert _run_child_and_sigterm_it(install_handler=False, tmp_path=tmp_path) is True


@pytest.mark.skipif(sys.platform == "win32", reason="signal.SIGTERM semantics tested here are POSIX-specific")
def test_treat_sigterm_as_interrupt_prevents_the_orphan(tmp_path):
    assert _run_child_and_sigterm_it(install_handler=True, tmp_path=tmp_path) is False


def test_treat_sigterm_as_interrupt_is_safe_to_call_outside_the_main_thread():
    """Must never raise even where it can't actually install a handler
    (e.g. called from a non-main thread) - it's a best-effort setup step,
    not something callers need to guard themselves."""
    import threading

    errors = []
    t = threading.Thread(target=lambda: errors.append(_try(treat_sigterm_as_interrupt)))
    t.start()
    t.join()
    assert errors == [None]


def _try(fn):
    try:
        fn()
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
