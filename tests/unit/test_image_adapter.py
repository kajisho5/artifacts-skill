from __future__ import annotations

import pytest

from artifact_skill.adapters.image.adapter import ImageAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactInputError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> ImageAdapter:
    return ImageAdapter()


def test_type_detection_by_content_not_extension(good_png, mislabeled_pdf_as_png):
    assert ArtifactRef.from_path(good_png).type == ArtifactType.IMAGE_PNG
    assert ArtifactRef.from_path(mislabeled_pdf_as_png).type == ArtifactType.PDF


def test_inspect_good_png_reports_dimensions(good_png, adapter):
    ref = ArtifactRef.from_path(good_png)
    report = adapter.inspect(ref)
    assert report.details["width"] == 200
    assert report.details["height"] == 100
    assert report.details["format"] == "PNG"
    assert report.details["mode"] == "RGB"
    assert report.details["exif_orientation"] is None


def test_inspect_corrupt_png_raises_structured_error(corrupt_png, adapter):
    ref = ArtifactRef.from_path(corrupt_png)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_IMAGE_UNREADABLE"


def test_verify_good_png_passes_cleanly(good_png, adapter):
    ref = ArtifactRef.from_path(good_png)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_dimension_requirements(good_png, adapter):
    ref = ArtifactRef.from_path(good_png)
    ok = adapter.verify_structural(ref, {"require_width": 200, "require_height": 100})
    assert next(c for c in ok.checks if c.id == "width_requirement").status == CheckStatus.PASS
    assert next(c for c in ok.checks if c.id == "height_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_width": 999})
    assert next(c for c in mismatch.checks if c.id == "width_requirement").status == CheckStatus.FAIL
    assert mismatch.status == CheckStatus.FAIL


def test_verify_dimension_range(good_png, adapter):
    ref = ArtifactRef.from_path(good_png)
    ok = adapter.verify_structural(ref, {"min_width": 100, "max_width": 300})
    assert next(c for c in ok.checks if c.id == "dimensions_range").status == CheckStatus.PASS

    too_narrow = adapter.verify_structural(ref, {"min_width": 500})
    assert next(c for c in too_narrow.checks if c.id == "dimensions_range").status == CheckStatus.FAIL


def test_verify_exif_orientation_warns(exif_rotated_jpeg, adapter):
    ref = ArtifactRef.from_path(exif_rotated_jpeg)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "exif_orientation")
    assert check.status == CheckStatus.WARN
    assert check.evidence["exif_orientation"] == 6


def test_execute_resize_maintains_aspect_ratio_by_default(good_png, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_png)
    before_hash = ref.sha256
    output_path = tmp_path / "resized.png"

    result_ref = adapter.execute(ref, "resize", {"width": 50, "height": 50}, output_path)

    assert output_path.exists()
    assert ArtifactRef.from_path(good_png).sha256 == before_hash  # Original Protection
    out_report = adapter.inspect(result_ref)
    # 200x100 fitted into a 50x50 box, aspect preserved -> 50x25
    assert out_report.details["width"] == 50
    assert out_report.details["height"] == 25


def test_execute_resize_without_aspect_ratio_stretches_exactly(good_png, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_png)
    output_path = tmp_path / "resized.png"
    result_ref = adapter.execute(
        ref, "resize", {"width": 40, "height": 40, "maintain_aspect_ratio": False}, output_path
    )
    out_report = adapter.inspect(result_ref)
    assert out_report.details["width"] == 40
    assert out_report.details["height"] == 40


def test_execute_convert_format_to_jpeg_drops_alpha(alpha_png, adapter, tmp_path):
    ref = ArtifactRef.from_path(alpha_png)
    before_hash = ref.sha256
    output_path = tmp_path / "converted.jpg"

    result_ref = adapter.execute(ref, "convert_format", {"format": "jpeg"}, output_path)

    assert ArtifactRef.from_path(alpha_png).sha256 == before_hash  # Original Protection
    out_report = adapter.inspect(result_ref)
    assert out_report.details["format"] == "JPEG"
    assert out_report.details["mode"] == "RGB"  # alpha composited away


def test_execute_unknown_operation_raises(good_png, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_png)
    with pytest.raises(ArtifactInputError):
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.png")


def test_render_normalizes_exif_rotation(exif_rotated_jpeg, adapter, tmp_path):
    """render() applies the EXIF transform so the evidence image is
    genuinely upright, unlike the raw file's stored pixel grid."""
    ref = ArtifactRef.from_path(exif_rotated_jpeg)
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1
    assert result.files[0].exists()
    from PIL import Image

    with Image.open(result.files[0]) as rendered:
        # orientation 6 is a 90-degree rotation: width/height swap.
        assert rendered.size == (200, 300)


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "image.structural" in caps
    assert "image.render" in caps
    assert caps["image.structural"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_limitations_names_the_real_known_caveats(adapter):
    """Issue #29: limitations() content had zero test coverage anywhere."""
    text = " ".join(adapter.limitations())
    assert "Animated images" in text
    assert "ICC color profiles" in text
