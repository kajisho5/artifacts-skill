"""The only place in this codebase allowed to call subprocess.

Rules (spec #9, #26):
  - argv list only, never `shell=True`, never string concatenation.
  - the executable must be on an explicit allowlist passed in by the caller
    (adapters declare their own small allowlist, e.g. {"soffice"}).
  - always runs with a timeout.
  - always runs in an isolated temporary working directory unless the
    caller explicitly provides one.
  - stdout/stderr are captured, never streamed to the user's shell.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from artifact_skill.core.errors import ArtifactExecutionError, ArtifactSecurityError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits


@dataclass
class ExecResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def which_allowed(executable: str) -> str | None:
    """Resolve an executable to an absolute path, or None if not on PATH.

    Callers must still check the result against their own allowlist of
    *names* before calling `run()` — this only resolves, it does not permit.
    """
    return shutil.which(executable)


def run(
    argv: list[str],
    *,
    allowlist: set[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ExecResult:
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ArtifactSecurityError(
            code="ARTIFACT_SUBPROCESS_INVALID_ARGV",
            message="argv must be a non-empty list of strings.",
            evidence={"argv": argv},
        )
    exe_name = Path(argv[0]).name
    if exe_name not in allowlist:
        raise ArtifactSecurityError(
            code="ARTIFACT_SUBPROCESS_NOT_ALLOWLISTED",
            message=f"Executable '{exe_name}' is not on the allowlist for this operation.",
            remediation=f"Allowed executables: {sorted(allowlist)}.",
            evidence={"executable": exe_name, "allowlist": sorted(allowlist)},
        )
    resolved = which_allowed(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
    if resolved is None or not Path(resolved).exists():
        raise ArtifactExecutionError(
            code="ARTIFACT_EXECUTABLE_NOT_FOUND",
            message=f"Executable '{argv[0]}' was not found on PATH.",
            remediation="Run `artifact-skill doctor` to see which backends are installed.",
            evidence={"executable": argv[0]},
        )

    effective_timeout = timeout if timeout is not None else limits.subprocess_timeout_seconds
    argv = [resolved, *argv[1:]]

    with tempfile.TemporaryDirectory(prefix="artifact-skill-exec-") as tmp:
        run_cwd = str(cwd) if cwd is not None else tmp
        try:
            proc = subprocess.run(
                argv,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                shell=False,
                env=_minimal_env(),
            )
            return ExecResult(
                argv=argv, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, timed_out=False
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                argv=argv,
                returncode=-1,
                stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
                timed_out=True,
            )


def _minimal_env() -> dict[str, str]:
    """A small, explicit environment rather than inheriting everything —
    reduces the chance a subprocess picks up unexpected config/proxies."""
    import os

    keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k in keep}
