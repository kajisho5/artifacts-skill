"""Shared `ffprobe`/`ffmpeg` invocation for the media adapter
(`adapters/media/adapter.py`) — structural probing and one-frame
extraction, the same "shell out to an external binary through
`security/subprocess_exec.py`, surface a structured error on failure"
shape `rendering/office_convert.py` established for `soffice`.

Neither `ffprobe` nor `ffmpeg` is a Python package this project can
`pip install` — like LibreOffice and Chromium, both are external binaries
a caller's environment must provide. `doctor/detect.py` probes for both
(`backend.ffmpeg`/`backend.ffprobe`) the same way it probes for `soffice`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError, ArtifactInputError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.subprocess_exec import run as run_subprocess
from artifact_skill.security.subprocess_exec import which_allowed

FFMPEG_ALLOWLIST = {"ffmpeg", "ffprobe"}


def ffprobe_binary() -> str | None:
    return which_allowed("ffprobe")


def ffmpeg_binary() -> str | None:
    return which_allowed("ffmpeg")


def require_ffprobe_binary(capability_id: str) -> str:
    binary = ffprobe_binary()
    if not binary:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="No ffprobe binary found on PATH; cannot inspect media structurally.",
            remediation="Install FFmpeg (which bundles ffprobe) and re-run `artifacts-skill doctor`.",
            evidence={"capability_id": capability_id},
        )
    return binary


def require_ffmpeg_binary(capability_id: str) -> str:
    binary = ffmpeg_binary()
    if not binary:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="No ffmpeg binary found on PATH; cannot render a video frame.",
            remediation="Install FFmpeg and re-run `artifacts-skill doctor`.",
            evidence={"capability_id": capability_id},
        )
    return binary


def probe_media(path: Path, *, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Runs `ffprobe -show_format -show_streams` and returns the parsed
    JSON. Raises `ArtifactInputError` (code `ARTIFACT_MEDIA_UNREADABLE`) if
    ffprobe exits non-zero or its own stdout isn't valid JSON — both mean
    the file isn't real, readable media despite matching a media
    container's magic bytes at the type-detection layer (a truncated or
    otherwise corrupt file can still start with a valid `ftyp`/EBML/RIFF
    header).
    """
    ffprobe = require_ffprobe_binary("media.structural")
    # See extract_frame()'s identical .resolve() for why: run_subprocess()
    # runs ffprobe inside its own fresh temp cwd, so a relative `path`
    # would otherwise be looked up from the wrong directory (confirmed by
    # direct reproduction against every fixture in this module's test
    # suite before this fix — same root cause as office_convert.py's
    # identical input_path issue, fixed alongside this one).
    path = path.resolve()
    result = run_subprocess(
        [
            # Argument-injection review finding: `path` is always .resolve()d
            # above so it can never start with "-", but the explicit "--"
            # removes the dependency on that ordering ever holding — a bare
            # positional path argument is otherwise always at risk of being
            # misread as an option by ffprobe's own arg parser.
            ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", "--", str(path),
        ],
        allowlist=FFMPEG_ALLOWLIST,
        limits=limits,
    )
    if result.returncode != 0:
        raise ArtifactInputError(
            code="ARTIFACT_MEDIA_UNREADABLE",
            message=f"ffprobe could not read '{path}' (exited {result.returncode}).",
            remediation="The file may be corrupt or truncated despite matching a media container's magic bytes.",
            evidence={"path": str(path), "returncode": result.returncode, "stderr": result.stderr},
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactInputError(
            code="ARTIFACT_MEDIA_UNREADABLE",
            message=f"ffprobe's own output for '{path}' was not valid JSON: {exc}",
            evidence={"path": str(path)},
        ) from exc


def extract_frame(path: Path, out_path: Path, *, at_seconds: float, limits: Limits = DEFAULT_LIMITS) -> None:
    """Extracts one frame at `at_seconds` into `out_path` (PNG) via
    `ffmpeg -ss <t> -i <path> -frames:v 1`. Raises `ArtifactExecutionError`
    (code `ARTIFACT_RENDER_BACKEND_FAILED`, carrying ffmpeg's own stdout/
    stderr) if ffmpeg exits non-zero or produces no output file — the
    same "surface the real diagnostic, never a silent empty result" shape
    `office_convert.py::convert_to_pdf()` uses for `soffice`.
    """
    ffmpeg = require_ffmpeg_binary("media.render")
    # run_subprocess() runs ffmpeg inside its own fresh temp cwd (not the
    # caller's) - a relative `path` must be resolved first, or ffmpeg looks
    # for it in the wrong directory. out_path is always already absolute
    # in every caller here (see adapters/media/adapter.py), but resolving
    # it too costs nothing and removes the same class of risk if that
    # ever stops being true.
    path = path.resolve()
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_subprocess(
        [
            # `path` is the value of an explicit -i flag (never a bare
            # positional), and `out_path` is always a hardcoded
            # "frame-001.png" basename resolved to absolute — neither can
            # currently start with "-", but the "--" removes the dependency
            # on that ordering ever holding for the output path.
            ffmpeg, "-y", "-ss", f"{max(at_seconds, 0.0):.3f}", "-i", str(path),
            "-frames:v", "1", "-f", "image2", "--", str(out_path),
        ],
        allowlist=FFMPEG_ALLOWLIST,
        limits=limits,
    )
    if result.returncode != 0 or not out_path.is_file():
        raise ArtifactExecutionError(
            code="ARTIFACT_RENDER_BACKEND_FAILED",
            message=f"ffmpeg failed to extract a frame from '{path}' at {at_seconds:.2f}s "
            f"(exited {result.returncode}).",
            remediation="See evidence.stdout/stderr for ffmpeg's own diagnostic.",
            evidence={"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
        )
