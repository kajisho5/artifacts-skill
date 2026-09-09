from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.adapters.media.adapter import MediaAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> MediaAdapter:
    return MediaAdapter()


def _probe_media_render_works(adapter: MediaAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    """Same pattern as the LibreOffice/Chromium-backed adapters' render
    probes — a present ffmpeg/ffprobe on PATH doesn't guarantee a specific
    environment's install actually works."""
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


# ---- type detection --------------------------------------------------


def test_type_detection_mp4_by_content_not_extension(good_mp4, mislabeled_pdf_as_mp4):
    assert ArtifactRef.from_path(good_mp4).type == ArtifactType.MEDIA
    assert ArtifactRef.from_path(mislabeled_pdf_as_mp4).type == ArtifactType.PDF


def test_type_detection_webm_and_wav(good_webm, good_wav):
    assert ArtifactRef.from_path(good_webm).type == ArtifactType.MEDIA
    assert ArtifactRef.from_path(good_wav).type == ArtifactType.MEDIA


def test_type_detection_does_not_misclassify_a_heic_ftyp_brand(tmp_path):
    """core/artifact.py's _FTYP_MEDIA_BRANDS finding: HEIC/AVIF still
    images also use the ISO-BMFF 'ftyp' box - without an allowlist of
    known media brands, a HEIC photo would be misdetected as MEDIA and
    then fail ffprobe's stream check as if it were corrupt media, rather
    than being honestly UNKNOWN (this project has no HEIC/AVIF adapter)."""
    path = tmp_path / "photo.heic"
    # Real ISO-BMFF layout: 4-byte box size, "ftyp", then the major brand.
    path.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16)
    assert ArtifactRef.from_path(path).type == ArtifactType.UNKNOWN


def test_type_detection_fails_closed_on_an_unrecognized_ftyp_brand(tmp_path):
    """Security-review finding: this used to be a denylist of known
    non-media brands (fail-open — anything not already excluded reached
    the Media adapter's ffprobe/ffmpeg invocation by default). Flipped to
    an allowlist of known media brands, verified here by direct
    reproduction: a made-up, unrecognized major brand must fail closed to
    UNKNOWN, not be handed to ffprobe/ffmpeg on the strength of merely
    not being on an exclusion list."""
    path = tmp_path / "unknownbrand.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypzzzz" + b"\x00" * 16)
    assert ArtifactRef.from_path(path).type == ArtifactType.UNKNOWN


# ---- inspect -----------------------------------------------------


def test_inspect_good_mp4_reports_video_and_audio_streams(good_mp4, adapter):
    ref = ArtifactRef.from_path(good_mp4)
    report = adapter.inspect(ref)
    details = report.details
    assert details["has_video"] is True
    assert details["has_audio"] is True
    assert details["video"]["codec_name"] == "h264"
    assert details["video"]["width"] == 320
    assert details["video"]["height"] == 240
    assert details["audio"]["codec_name"] == "aac"
    assert details["duration_seconds"] == pytest.approx(2.0, abs=0.2)
    assert details["leftover_markers"] == []


def test_inspect_good_webm_is_video_only(good_webm, adapter):
    ref = ArtifactRef.from_path(good_webm)
    details = adapter.inspect(ref).details
    assert details["has_video"] is True
    assert details["has_audio"] is False
    assert details["audio"] is None
    assert details["video"]["codec_name"] == "vp9"


def test_inspect_good_wav_is_audio_only(good_wav, adapter):
    ref = ArtifactRef.from_path(good_wav)
    details = adapter.inspect(ref).details
    assert details["has_video"] is False
    assert details["has_audio"] is True
    assert details["video"] is None
    assert details["audio"]["codec_name"] == "pcm_s16le"


def test_inspect_audio_only_mp4_has_no_video_stream(audio_only_mp4, adapter):
    """Proves has_video/has_audio come from the real probed streams, not
    assumed from the container family (MP4 is usually thought of as
    video, but an M4A-shaped audio-only file is a real, common case)."""
    ref = ArtifactRef.from_path(audio_only_mp4)
    details = adapter.inspect(ref).details
    assert details["has_video"] is False
    assert details["has_audio"] is True


def test_inspect_audio_with_cover_art_is_not_reported_as_having_video(audio_with_cover_m4a, adapter):
    """Adversarial-review finding, verified by direct reproduction before
    this fix: an audio file with embedded cover art (extremely common -
    iTunes/Apple Music/podcast-tool M4A, ripped MP3/M4A with album art)
    gets an mjpeg "video" stream from ffprobe alongside the real audio
    stream, marked disposition.attached_pic=1. Without excluding it,
    this file was misreported as has_video=True."""
    ref = ArtifactRef.from_path(audio_with_cover_m4a)
    details = adapter.inspect(ref).details
    assert details["has_video"] is False
    assert details["has_audio"] is True
    assert details["video"] is None


def test_verify_require_has_video_fails_for_cover_art_only(audio_with_cover_m4a, adapter):
    ref = ArtifactRef.from_path(audio_with_cover_m4a)
    result = adapter.verify_structural(ref, {"require_has_video": True})
    check = next(c for c in result.checks if c.id == "has_video")
    assert check.status == CheckStatus.FAIL


def test_render_audio_with_cover_art_takes_the_audio_only_path_not_a_crash(audio_with_cover_m4a, adapter, tmp_path):
    """Before the _first_stream() fix, render() tried to seek to a
    mid-file timestamp the attached-pic stream has no real frame at,
    raising ArtifactExecutionError for a perfectly ordinary, valid audio
    file instead of taking the graceful audio-only path."""
    ref = ArtifactRef.from_path(audio_with_cover_m4a)
    result = adapter.render(ref, tmp_path / "rendered")
    assert result.files == []
    assert any("audio-only" in w for w in result.warnings)


def test_render_single_frame_short_video_falls_back_to_t_zero(single_frame_mp4, adapter, tmp_path):
    """Adversarial-review finding, verified by direct reproduction before
    this fix: a short/single-frame-ish clip can genuinely have no frame
    at render()'s computed midpoint seek time (only a real frame at
    t=0), so ffmpeg exits 0 with no output there even though the file
    is not corrupt. render() must retry at t=0 rather than raising."""
    ref = ArtifactRef.from_path(single_frame_mp4)
    if not _probe_media_render_works(adapter, ref, tmp_path):
        pytest.skip("ffmpeg in this environment cannot render (see adapter module docstring)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists() and result.files[0].stat().st_size > 0


def test_inspect_rejects_an_oversized_declared_resolution(oversized_resolution_mp4, adapter):
    """Security-review finding, verified by direct reproduction before
    this fix: a container can declare an enormous *decoded* frame size
    while compressing to almost nothing on disk (a solid-color frame is
    trivially compressible) - a 12000x12000 real MP4 built this way made
    ffprobe alone peak at ~396MB RSS and the full render() path peak at
    ~1.4GB RSS over ~16s, from a ~28KB file - a decompression-bomb shape
    analogous to the zip-bomb guard other adapters already have. Checked
    in inspect() (not just render()) so verify_structural() inherits the
    guard too, and propagates uncaught (a security control, not a mere
    FAIL Check), matching the SVG/XLSX/EPUB precedent for this class of
    risk."""
    ref = ArtifactRef.from_path(oversized_resolution_mp4)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_MEDIA_RESOLUTION_TOO_LARGE"

    with pytest.raises(ArtifactSecurityError):
        adapter.verify_structural(ref, {})


def test_inspect_corrupt_truncated_mp4_raises(corrupt_truncated_mp4, adapter):
    ref = ArtifactRef.from_path(corrupt_truncated_mp4)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_MEDIA_UNREADABLE"


def test_inspect_garbage_too_short_raises(garbage_too_short_mp4, adapter):
    ref = ArtifactRef.from_path(garbage_too_short_mp4)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_MEDIA_UNREADABLE"


def test_inspect_reports_leftover_placeholder_metadata_tag(leftover_placeholder_wav, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_wav)
    details = adapter.inspect(ref).details
    assert "todo" in details["leftover_markers"]


# ---- verify_structural ---------------------------------------------


def test_verify_good_mp4_passes_with_no_policy(good_mp4, adapter):
    ref = ArtifactRef.from_path(good_mp4)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_corrupt_file_fails_media_readable(corrupt_truncated_mp4, adapter):
    ref = ArtifactRef.from_path(corrupt_truncated_mp4)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "media_readable")
    assert check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_verify_duration_range_policy(good_mp4, adapter):
    ref = ArtifactRef.from_path(good_mp4)
    ok = adapter.verify_structural(ref, {"min_duration_seconds": 1.0, "max_duration_seconds": 5.0})
    assert next(c for c in ok.checks if c.id == "duration_range").status == CheckStatus.PASS

    too_strict = adapter.verify_structural(ref, {"min_duration_seconds": 10.0})
    assert next(c for c in too_strict.checks if c.id == "duration_range").status == CheckStatus.FAIL


def test_verify_require_has_video_and_has_audio(good_mp4, good_wav, adapter):
    video_ref = ArtifactRef.from_path(good_mp4)
    audio_ref = ArtifactRef.from_path(good_wav)

    assert next(
        c for c in adapter.verify_structural(video_ref, {"require_has_video": True}).checks if c.id == "has_video"
    ).status == CheckStatus.PASS
    assert next(
        c for c in adapter.verify_structural(audio_ref, {"require_has_video": True}).checks if c.id == "has_video"
    ).status == CheckStatus.FAIL
    assert next(
        c for c in adapter.verify_structural(audio_ref, {"require_has_audio": True}).checks if c.id == "has_audio"
    ).status == CheckStatus.PASS


def test_verify_require_min_resolution(good_mp4, adapter):
    ref = ArtifactRef.from_path(good_mp4)
    ok = adapter.verify_structural(ref, {"require_min_resolution": (320, 240)})
    assert next(c for c in ok.checks if c.id == "min_resolution").status == CheckStatus.PASS

    too_big = adapter.verify_structural(ref, {"require_min_resolution": (1920, 1080)})
    assert next(c for c in too_big.checks if c.id == "min_resolution").status == CheckStatus.FAIL


def test_verify_require_min_resolution_fails_with_no_video_stream(good_wav, adapter):
    ref = ArtifactRef.from_path(good_wav)
    result = adapter.verify_structural(ref, {"require_min_resolution": (1, 1)})
    check = next(c for c in result.checks if c.id == "min_resolution")
    assert check.status == CheckStatus.FAIL
    assert "no video stream" in check.message


def test_verify_require_video_and_audio_codec(good_mp4, adapter):
    ref = ArtifactRef.from_path(good_mp4)
    ok = adapter.verify_structural(ref, {"require_video_codec": ["h264"], "require_audio_codec": ["aac"]})
    assert next(c for c in ok.checks if c.id == "video_codec").status == CheckStatus.PASS
    assert next(c for c in ok.checks if c.id == "audio_codec").status == CheckStatus.PASS

    wrong = adapter.verify_structural(ref, {"require_video_codec": "vp9"})
    assert next(c for c in wrong.checks if c.id == "video_codec").status == CheckStatus.FAIL


def test_verify_forbid_placeholder_text_escalates_to_fail(leftover_placeholder_wav, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_wav)
    warn_result = adapter.verify_structural(ref, {})
    assert next(c for c in warn_result.checks if c.id == "leftover_placeholder_text").status == CheckStatus.WARN

    fail_result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    assert next(c for c in fail_result.checks if c.id == "leftover_placeholder_text").status == CheckStatus.FAIL


# ---- render ------------------------------------------------------


def test_render_extracts_one_frame_from_good_mp4(good_mp4, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_mp4)
    if not _probe_media_render_works(adapter, ref, tmp_path):
        pytest.skip("ffmpeg in this environment cannot render (see adapter module docstring)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists() and result.files[0].stat().st_size > 0


def test_render_audio_only_produces_no_files_with_a_warning(good_wav, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_wav)
    result = adapter.render(ref, tmp_path / "rendered")
    assert result.files == []
    assert any("audio-only" in w for w in result.warnings)


def test_render_rejects_corrupt_input(corrupt_truncated_mp4, adapter, tmp_path):
    ref = ArtifactRef.from_path(corrupt_truncated_mp4)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_MEDIA_UNREADABLE"


# ---- plan / execute (no mutating operations) ------------------------


def test_plan_rejects_any_operation(good_mp4, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_mp4)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.plan(ref, "metadata_set", {"title": "x"}, tmp_path / "out.mp4")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"


def test_execute_rejects_any_operation(good_mp4, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_mp4)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "metadata_set", {"title": "x"}, tmp_path / "out.mp4")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"


def test_operations_is_empty(adapter):
    assert adapter.operations() == {}


# ---- misc ------------------------------------------------------


def test_limitations_names_the_real_known_caveats(adapter):
    text = " ".join(adapter.limitations())
    assert "No mutating operations" in text
    assert "one frame" in text
    assert "MP3" in text


def test_capabilities_report_structural_and_render(adapter):
    caps = adapter.capabilities()
    ids = {c.id for c in caps}
    assert ids == {"media.structural", "media.render"}
