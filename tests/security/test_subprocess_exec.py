from __future__ import annotations

import pytest

from artifact_skill.core.errors import ArtifactExecutionError, ArtifactSecurityError
from artifact_skill.security.subprocess_exec import run


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
