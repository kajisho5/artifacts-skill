"""Media (video/audio) adapter.

Structural inspection and rendering both shell out to `ffprobe`/`ffmpeg`
(`rendering/ffmpeg_probe.py`) — neither is a Python package this project
can bundle, so `media.structural`/`media.render` report `AVAILABLE` only
when the corresponding binary is actually found on PATH, the same
present-binary-is-not-a-guarantee posture `adapters/pptx/adapter.py`
documents for LibreOffice.

**No mutating operations, by design, not by omission**: this project is a
verification/evidence-generation engine, not a video editor (see
docs/architecture.md's "What this is (and isn't)"). Producing or editing
media is squarely another tool's job (an ffmpeg-driven generation skill,
for instance) — this adapter's contract is the same one HTML/SVG/CSV/
Markdown already have: inspect/render/verify, never execute.

**`ArtifactType.MEDIA` covers both audio and video** (MP4/MOV/M4A-family,
WebM/Matroska, WAV) rather than splitting into separate types, because
type detection (`core/artifact.py::detect_type()`) can only recognize the
*container* from magic bytes — telling audio-only from video apart
requires actually probing streams, which is exactly what `inspect()`
does. `details["has_video"]`/`details["has_audio"]` are the real answer;
the type is honestly "this is some ffprobe-readable media container."

**One extracted frame is the render, not a full playback check**: the
same "give an agent something it can actually look at" contract every
other adapter's `render()` honors, applied to video's own natural
analogue of "a page" - see `limitations()` for what this deliberately
does not verify (audio content, motion, playback smoothness).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.ffmpeg_probe import extract_frame, ffmpeg_binary, ffprobe_binary, probe_media
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.paths import check_input_size


def _parse_frame_rate(value: str | None) -> float | None:
    """ffprobe reports frame rate as a "num/den" string (e.g. "30/1", or
    "0/0" when genuinely unknown, which this returns as None rather than
    raising a ZeroDivisionError)."""
    if not value or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return None
    if den_f == 0:
        return None
    return num_f / den_f


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    """Adversarial-review finding, verified by direct reproduction: an
    audio file with embedded cover art (extremely common — iTunes/Apple
    Music/podcast-tool M4A, ripped MP3/M4A with album art) has an mjpeg
    "video" stream ffprobe reports alongside the real audio stream. Its
    `disposition.attached_pic == 1` marks it as a still image attached to
    the file, not real video content — without excluding it here, such a
    file was misreported as `has_video: True` (a `require_has_video`
    policy false-PASSed), and render() then tried to seek to a mid-file
    timestamp the attached-pic stream has no frame at, crashing with
    `ARTIFACT_RENDER_BACKEND_FAILED` instead of taking the ordinary
    audio-only "zero files, one warning" path."""
    return next(
        (s for s in streams if s.get("codec_type") == codec_type and not s.get("disposition", {}).get("attached_pic")),
        None,
    )


class MediaAdapter(ArtifactAdapter):
    id = "media"
    artifact_type = ArtifactType.MEDIA

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.MEDIA

    def operations(self) -> dict[str, OperationSpec]:
        return {}

    def capabilities(self) -> list[Capability]:
        structural = (
            Capability(
                id="media.structural", status=CapabilityStatus.AVAILABLE,
                detail=f"ffprobe found on PATH: {ffprobe_binary()}",
                detected_via="which ffprobe",
            )
            if ffprobe_binary()
            else Capability(
                id="media.structural", status=CapabilityStatus.MISSING,
                detail="ffprobe not found on PATH.", detected_via="which ffprobe",
            )
        )
        if ffmpeg_binary() and ffprobe_binary():
            render = Capability(
                id="media.render", status=CapabilityStatus.AVAILABLE,
                detail=f"ffmpeg found on PATH: {ffmpeg_binary()}. A present binary does not guarantee a specific "
                "file converts successfully — see adapters/pptx/adapter.py's docstring for the same caveat "
                "applied to LibreOffice.",
                detected_via="which ffmpeg",
            )
        else:
            render = Capability(
                id="media.render", status=CapabilityStatus.MISSING,
                detail="ffmpeg and/or ffprobe not found on PATH.", detected_via="which ffmpeg",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "No mutating operations: this adapter inspects/renders/verifies media, it does not transcode, "
            "trim, or otherwise edit it — that is squarely a generation/editing tool's job, not this "
            "project's (see docs/architecture.md).",
            "render() extracts exactly one frame as visual evidence, not a full playback check — motion, "
            "audio content, and frame-to-frame consistency are never verified. An audio-only file (no video "
            "stream) has nothing to extract a frame from; render() reports zero files with a warning, not an error.",
            "Type detection recognizes MP4/MOV/M4A-family (ISO-BMFF 'ftyp'), WebM/Matroska (EBML), and WAV "
            "(RIFF/WAVE) container magic bytes only — MP3 (no reliable magic-byte signature without deeper "
            "frame parsing) and other containers are not recognized as MEDIA and fall through to UNKNOWN.",
            "Corruption/unreadability detection relies entirely on ffprobe's own exit code and JSON output — "
            "a file ffprobe can open but that plays back incorrectly in some other player is not detected.",
            "Leftover-placeholder-text scanning covers container-level metadata tag *values* (title, comment, "
            "artist, etc., whatever ffprobe reports under format.tags) - not any text that might appear "
            "burned into the video frames themselves, which no OCR step here reads.",
        ]

    def recognized_policy_keys(self) -> frozenset[str]:
        return frozenset({
            "min_duration_seconds", "max_duration_seconds", "require_has_video", "require_has_audio",
            "require_min_resolution", "require_video_codec", "require_audio_codec", "forbid_placeholder_text",
        })

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        probe = probe_media(ref.path)
        fmt = probe.get("format", {})
        streams = probe.get("streams", [])

        video = _first_stream(streams, "video")
        audio = _first_stream(streams, "audio")

        if video is not None:
            width, height = video.get("width"), video.get("height")
            if isinstance(width, int) and isinstance(height, int) and width * height > DEFAULT_LIMITS.max_video_pixels:
                # Security-review finding, verified by direct reproduction:
                # a container can declare an enormous *decoded* frame size
                # while compressing to almost nothing on disk (a solid-color
                # frame is trivially compressible) - checked here, right
                # after ffprobe returns and before anything more expensive
                # (render()'s actual frame decode) runs, so both
                # verify_structural() and render() inherit the guard via
                # this shared inspect() rather than each needing their own.
                raise ArtifactSecurityError(
                    code="ARTIFACT_MEDIA_RESOLUTION_TOO_LARGE",
                    message=f"'{ref.path}' declares a {width}x{height} video frame "
                    f"({width * height} pixels), exceeding the limit of {DEFAULT_LIMITS.max_video_pixels}.",
                    remediation="Not decoded; this may be a decompression-bomb-shaped file. Increase "
                    "Limits.max_video_pixels if this resolution is legitimately expected.",
                    evidence={"path": str(ref.path), "width": width, "height": height},
                )

        duration_raw = fmt.get("duration")
        try:
            duration_seconds = float(duration_raw) if duration_raw is not None else None
        except ValueError:
            duration_seconds = None

        bit_rate_raw = fmt.get("bit_rate")
        try:
            bit_rate = int(bit_rate_raw) if bit_rate_raw is not None else None
        except ValueError:
            bit_rate = None

        tags = fmt.get("tags", {}) or {}

        details: dict[str, Any] = {
            "format_name": fmt.get("format_name"),
            "duration_seconds": duration_seconds,
            "bit_rate": bit_rate,
            "size_bytes": ref.size_bytes,
            "stream_count": len(streams),
            "has_video": video is not None,
            "has_audio": audio is not None,
            "video": None
            if video is None
            else {
                "codec_name": video.get("codec_name"),
                "width": video.get("width"),
                "height": video.get("height"),
                "frame_rate": _parse_frame_rate(video.get("avg_frame_rate")),
                "pix_fmt": video.get("pix_fmt"),
            },
            "audio": None
            if audio is None
            else {
                "codec_name": audio.get("codec_name"),
                "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
                "channels": audio.get("channels"),
            },
            "tags": tags,
            "leftover_markers": find_leftover_markers("\n".join(str(v) for v in tags.values())),
        }
        return InspectionReport(artifact=ref, details=details)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"Media adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"Media adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    # ---- render ------------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> RenderResult:
        check_input_size(ref.path, limits)
        report = self.inspect(ref)
        details = report.details

        out_dir.mkdir(parents=True, exist_ok=True)

        if not details["has_video"]:
            return RenderResult(
                kind="page_images", files=[], backend="ffmpeg",
                warnings=["No video stream to extract a frame from (audio-only media)."],
            )

        duration = details["duration_seconds"]
        at_seconds = min(2.0, duration / 2) if duration and duration > 0 else 0.0

        out_path = out_dir / "frame-001.png"
        try:
            extract_frame(ref.path, out_path, at_seconds=at_seconds, limits=limits)
        except ArtifactExecutionError:
            # Adversarial-review finding, verified by direct reproduction:
            # a short/single-frame-ish clip can genuinely have no frame at
            # the computed midpoint (e.g. a real frame only at t=0), so
            # ffmpeg exits 0 with no output there even though the file is
            # not corrupt. Retry at t=0 (skipped above if that's already
            # what was tried) before giving up - real content, just seeked
            # to a timestamp that doesn't land on a frame.
            if at_seconds == 0.0:
                return RenderResult(
                    kind="page_images", files=[], backend="ffmpeg",
                    warnings=["ffmpeg could not extract a frame from this video (no frame found at t=0)."],
                )
            try:
                extract_frame(ref.path, out_path, at_seconds=0.0, limits=limits)
            except ArtifactExecutionError:
                return RenderResult(
                    kind="page_images", files=[], backend="ffmpeg",
                    warnings=["ffmpeg could not extract a frame from this video at the midpoint or at t=0."],
                )
        return RenderResult(kind="page_images", files=[out_path], backend="ffmpeg")

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="media_readable", name="Media is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="media_readable", name="Media is readable", status=CheckStatus.PASS))

        details = report.details
        duration = details["duration_seconds"]
        if duration is None:
            checks.append(
                Check(
                    id="duration_known", name="Duration is known", status=CheckStatus.UNKNOWN,
                    message="ffprobe did not report a duration for this file.",
                )
            )
        elif duration <= 0:
            checks.append(
                Check(
                    id="duration_known", name="Duration is known", status=CheckStatus.FAIL,
                    message=f"Reported duration is {duration}s — a real media file should not be zero-length.",
                    evidence={"duration_seconds": duration},
                )
            )
        else:
            checks.append(
                Check(
                    id="duration_known", name="Duration is known", status=CheckStatus.PASS,
                    message=f"{duration:.2f}s.", evidence={"duration_seconds": duration},
                )
            )

        if details["stream_count"] == 0:
            checks.append(
                Check(
                    id="has_stream", name="Container has at least one audio/video stream", status=CheckStatus.FAIL,
                    message="ffprobe opened the file but found zero streams — likely truncated or corrupt.",
                )
            )
        else:
            checks.append(
                Check(id="has_stream", name="Container has at least one audio/video stream", status=CheckStatus.PASS)
            )

        if "min_duration_seconds" in policy or "max_duration_seconds" in policy:
            lo = policy.get("min_duration_seconds")
            hi = policy.get("max_duration_seconds")
            if duration is None:
                # duration_known above already reported UNKNOWN for the
                # underlying reason — this range check inherits that same
                # UNKNOWN rather than FAILing a requirement it never
                # actually got to evaluate.
                status = CheckStatus.UNKNOWN
            else:
                ok = (lo is None or duration >= lo) and (hi is None or duration <= hi)
                status = CheckStatus.PASS if ok else CheckStatus.FAIL
            checks.append(
                Check(
                    id="duration_range", name="Duration within required range", status=status,
                    message=f"Expected [{lo}, {hi}]s, found {duration}.",
                    evidence={"min": lo, "max": hi, "actual": duration},
                )
            )

        if policy.get("require_has_video"):
            checks.append(
                Check(
                    id="has_video", name="Has a video stream",
                    status=CheckStatus.PASS if details["has_video"] else CheckStatus.FAIL,
                    message="Video stream present." if details["has_video"] else "No video stream found.",
                )
            )

        if policy.get("require_has_audio"):
            checks.append(
                Check(
                    id="has_audio", name="Has an audio stream",
                    status=CheckStatus.PASS if details["has_audio"] else CheckStatus.FAIL,
                    message="Audio stream present." if details["has_audio"] else "No audio stream found.",
                )
            )

        if "require_min_resolution" in policy:
            min_w, min_h = policy["require_min_resolution"]
            video = details["video"]
            ok = video is not None and (video["width"] or 0) >= min_w and (video["height"] or 0) >= min_h
            actual = f"{video['width']}x{video['height']}" if video else "no video stream"
            checks.append(
                Check(
                    id="min_resolution", name="Video resolution meets minimum",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"Expected at least {min_w}x{min_h}, found {actual}.",
                    evidence={"expected_min": [min_w, min_h], "actual": actual},
                )
            )

        if "require_video_codec" in policy:
            allowed = policy["require_video_codec"]
            allowed = {allowed} if isinstance(allowed, str) else set(allowed)
            video = details["video"]
            actual_codec = video["codec_name"] if video else None
            ok = actual_codec in allowed
            checks.append(
                Check(
                    id="video_codec", name="Video codec matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"Expected one of {sorted(allowed)}, found {actual_codec!r}.",
                    evidence={"expected": sorted(allowed), "actual": actual_codec},
                )
            )

        if "require_audio_codec" in policy:
            allowed = policy["require_audio_codec"]
            allowed = {allowed} if isinstance(allowed, str) else set(allowed)
            audio = details["audio"]
            actual_codec = audio["codec_name"] if audio else None
            ok = actual_codec in allowed
            checks.append(
                Check(
                    id="audio_codec", name="Audio codec matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"Expected one of {sorted(allowed)}, found {actual_codec!r}.",
                    evidence={"expected": sorted(allowed), "actual": actual_codec},
                )
            )

        leftover_markers = details["leftover_markers"]
        if leftover_markers:
            checks.append(
                Check(
                    id="leftover_placeholder_text", name="No leftover generation placeholder text",
                    status=CheckStatus.FAIL if policy.get("forbid_placeholder_text") else CheckStatus.WARN,
                    message=f"Found likely-unreviewed placeholder text in metadata tags: {leftover_markers}.",
                    evidence={"markers": leftover_markers},
                )
            )
        else:
            checks.append(
                Check(id="leftover_placeholder_text", name="No leftover generation placeholder text", status=CheckStatus.PASS)
            )

        return VerificationResult(kind="structural", checks=checks)
