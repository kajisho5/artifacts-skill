"""Shared LibreOffice-headless conversion, used by every Office-family
adapter that renders via `format -> PDF -> pypdfium2 page images`
(PPTX today; DOCX; XLSX once it lands — see docs/roadmap.md).

Kept separate from `rendering/pdf_pages.py` because that module only
knows about PDFs; this one is specifically "how do we get a PDF out of a
non-PDF Office document," which is a different, LibreOffice-specific
concern with its own failure modes (see the module docstring below and
docs/adapters.md's PPTX section for the concrete one this project hit).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.subprocess_exec import run as run_subprocess

SOFFICE_ALLOWLIST = {"soffice", "libreoffice"}


def soffice_binary() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def require_soffice_binary(capability_id: str) -> str:
    binary = soffice_binary()
    if not binary:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="No soffice/libreoffice binary found on PATH; cannot render to images.",
            remediation="Install LibreOffice and re-run `artifacts-skill doctor`.",
            evidence={"capability_id": capability_id},
        )
    return binary


def convert_to_pdf(input_path: Path, pdf_out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> Path:
    """Convert `input_path` to PDF via LibreOffice headless, returning the
    produced PDF's path (inside a fresh, per-call `pdf_out_dir`).

    `limits.subprocess_timeout_seconds` governs the soffice call, via
    `run_subprocess()`'s own `limits` parameter — passed through explicitly
    here (rather than left to `run_subprocess()`'s default) so a caller's
    own `Limits` override actually reaches this subprocess, the same way
    the Chromium-backed render path now honors `render_timeout_seconds`
    (Issue #28).

    Raises `ArtifactCapabilityError` if no soffice binary is on PATH, or
    `ArtifactExecutionError` (code `ARTIFACT_RENDER_BACKEND_FAILED`,
    carrying soffice's own stdout/stderr in `evidence`) if the binary runs
    but produces no output — which does happen with an otherwise-healthy-
    looking LibreOffice install; see docs/adapters.md's PPTX section for
    the real instance of this that shaped this function's error handling.
    """
    soffice = require_soffice_binary("render")
    pdf_out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="artifacts-skill-soffice-profile-") as profile_dir:
        # Path.as_uri() (self-audit finding, FIX_PROMPT P1-3): naive
        # f"file://{profile_dir}" string concatenation is wrong on Windows
        # (needs "file:///C:/..." — an extra slash plus backslash-to-slash
        # conversion) and, even on POSIX, doesn't percent-encode a path
        # containing spaces or "#"/"?" (a "#" would be read as a URL
        # fragment separator, silently truncating the path — confirmed
        # directly against a real Chromium/file:// navigation while
        # auditing the sibling case in chromium_render.py below).
        # tempfile.TemporaryDirectory() always yields an absolute path, so
        # .as_uri() alone (no .resolve() needed) is correct here.
        profile_uri = Path(profile_dir).as_uri()
        result = run_subprocess(
            [
                soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to", "pdf", "--outdir", str(pdf_out_dir), str(input_path),
            ],
            allowlist=SOFFICE_ALLOWLIST,
            limits=limits,
        )
        produced = list(pdf_out_dir.glob("*.pdf"))
        if result.returncode != 0 or not produced:
            reason = (
                f"exited {result.returncode}" if result.returncode != 0
                else "exited 0 but produced no PDF output"
            )
            raise ArtifactExecutionError(
                code="ARTIFACT_RENDER_BACKEND_FAILED",
                message=f"LibreOffice failed to convert '{input_path}' to PDF ({reason}).",
                remediation="See evidence.stdout/stderr for LibreOffice's own diagnostic. This can mean "
                "a missing import filter, a broken LibreOffice profile, or an unsupported feature in the "
                "source file — not necessarily a problem with this adapter.",
                evidence={"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            )
        return produced[0]
