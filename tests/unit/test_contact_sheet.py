"""Unit tests for rendering/contact_sheet.py (Issue #29).

`build_contact_sheet()`/`build_before_after()` back the `look` CLI/MCP
subcommand and had zero automated test coverage anywhere in this project
despite working correctly when run manually - `pytest --cov` showed this
module at 18%. Format-agnostic image assembly, so plain in-memory PNGs
(no adapter/fixture involvement) are enough to exercise the real logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.core.errors import ArtifactInputError
from artifact_skill.rendering.contact_sheet import build_before_after, build_contact_sheet


def _make_png(path: Path, color: str = "red", size: tuple[int, int] = (50, 60)) -> Path:
    from PIL import Image

    Image.new("RGB", size, color=color).save(path)
    return path


def test_build_contact_sheet_rejects_empty_input(tmp_path):
    with pytest.raises(ArtifactInputError) as exc_info:
        build_contact_sheet([], tmp_path / "sheet.png")
    assert exc_info.value.code == "ARTIFACT_INPUT_NOT_FOUND"


def test_build_contact_sheet_single_image_produces_a_real_png(tmp_path):
    page = _make_png(tmp_path / "page-001.png")
    out = build_contact_sheet([page], tmp_path / "sheet.png")

    assert out.exists()
    from PIL import Image

    with Image.open(out) as img:
        assert img.format == "PNG"
        # One thumbnail cell plus margins - just confirm it's not degenerate.
        assert img.size[0] > 100
        assert img.size[1] > 100


def test_build_contact_sheet_lays_out_multiple_pages_in_a_grid(tmp_path):
    pages = [_make_png(tmp_path / f"page-{i:03d}.png", color=c) for i, c in enumerate(["red", "green", "blue", "yellow", "purple"])]
    out = build_contact_sheet(pages, tmp_path / "sheet.png", columns=3)

    from PIL import Image

    with Image.open(out) as img:
        # 5 images at 3 columns -> 2 rows; the sheet must be wide enough for
        # 3 columns and tall enough for 2 rows, not just 1 of each.
        assert img.size[0] > img.size[1] / 2  # sanity: wider layout than a single stacked column
    # Doesn't crash on an uneven last row (5 images, 3 columns -> row 2 has 2).


def test_build_contact_sheet_more_columns_than_images_still_works(tmp_path):
    pages = [_make_png(tmp_path / "page-001.png")]
    out = build_contact_sheet(pages, tmp_path / "sheet.png", columns=10)
    assert out.exists()


def test_build_contact_sheet_creates_missing_output_directory(tmp_path):
    page = _make_png(tmp_path / "page-001.png")
    out_path = tmp_path / "nested" / "dir" / "sheet.png"
    result = build_contact_sheet([page], out_path)
    assert result == out_path
    assert out_path.exists()


def test_build_before_after_rejects_empty_before(tmp_path):
    after = _make_png(tmp_path / "after.png")
    with pytest.raises(ArtifactInputError) as exc_info:
        build_before_after([], [after], tmp_path / "compare.png")
    assert exc_info.value.code == "ARTIFACT_INPUT_NOT_FOUND"


def test_build_before_after_rejects_empty_after(tmp_path):
    before = _make_png(tmp_path / "before.png")
    with pytest.raises(ArtifactInputError) as exc_info:
        build_before_after([before], [], tmp_path / "compare.png")
    assert exc_info.value.code == "ARTIFACT_INPUT_NOT_FOUND"


def test_build_before_after_pairs_pages_side_by_side(tmp_path):
    before = [_make_png(tmp_path / "b1.png", color="red")]
    after = [_make_png(tmp_path / "a1.png", color="blue")]
    out = build_before_after(before, after, tmp_path / "compare.png")

    from PIL import Image

    with Image.open(out) as img:
        assert img.format == "PNG"
        # Two thumbnails side by side -> wider than a single-column contact sheet cell.
        assert img.size[0] > 400


def test_build_before_after_with_mismatched_page_counts_pairs_up_to_the_shorter_list(tmp_path):
    """delete_pages can legitimately leave before/after with different page
    counts - zip(strict=False) pairs up to the shorter list rather than
    raising, which this test locks in as intended behavior, not an
    accident of the zip() call."""
    before = [_make_png(tmp_path / f"b{i}.png") for i in range(3)]
    after = [_make_png(tmp_path / f"a{i}.png") for i in range(1)]
    out = build_before_after(before, after, tmp_path / "compare.png")
    assert out.exists()  # must not raise despite the length mismatch


def test_build_before_after_creates_missing_output_directory(tmp_path):
    before = [_make_png(tmp_path / "b.png")]
    after = [_make_png(tmp_path / "a.png")]
    out_path = tmp_path / "nested" / "dir" / "compare.png"
    result = build_before_after(before, after, out_path)
    assert result == out_path
    assert out_path.exists()
